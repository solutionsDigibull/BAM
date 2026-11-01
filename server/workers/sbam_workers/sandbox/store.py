"""Where a sandbox run is recorded: its row, its stored evidence and its gate verdict.

The activity decides *what* happened; this module writes it down, in an order that survives a crash
between any two steps:

  1. `claim` takes the row (STARTED). A retry closes a stale STARTED row as SUPERSEDED and starts
     the next run number; a finished test outcome is handed back as a replay (see the repository).
  2. `record_vm` writes the VM id as soon as it exists, so the reaper can find a leaked VM.
  3. `finish` stores the evidence content-addressed (idempotent: same bytes, same key), indexes it
     in `artifacts`, writes the LOOP_TESTS verdict the corrector reads, and freezes the row, in one
     transaction. A row that is already frozen raises, so a late loser can never overwrite a winner.

Evidence is stored for every run that reached a VM, passed or failed: the FDE and the audit need
what the code printed and what the runner measured, not only a verdict.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sbam_api.artifacts.models import ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.sandbox.repository import Claim, RunOutcome, SandboxRunRepository
from sbam_api.storage.base import Kind, StorageBackend, StorageScope, StoredObject
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.sandbox import EgressEvent, FailureCategory, InfraReason, SandboxMode
from sqlalchemy.orm import Session, sessionmaker

from sbam_workers.sandbox.classify import Classified
from sbam_workers.sandbox.load import Identity

TOOL_VERSION: Final = "sandbox-adapter/1"
SYSTEM: Final = "SYSTEM"
JSON: Final = "application/json"
TEXT: Final = "text/plain; charset=utf-8"
JSONL: Final = "application/x-ndjson"


@dataclass(frozen=True)
class Evidence:
    outputs: bytes | None
    logs: bytes
    egress: tuple[EgressEvent, ...]
    truncated: bool


@dataclass(frozen=True)
class Claimed:
    run_id: uuid.UUID
    run_no: int
    replay: bool
    failure_category: FailureCategory | None  # a replay's outcome


class SqlRunStore:
    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    @staticmethod
    def _ctx(inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def claim(
        self,
        inp: BuildStageInput,
        identity: Identity,
        mode: SandboxMode,
        *,
        workflow_id: str,
        activity_id: str,
        activity_attempt: int,
    ) -> Claimed:
        with self.sessions() as session:
            claim: Claim = SandboxRunRepository(session, self._ctx(inp)).claim(
                code_attempt_id=identity.attempt_id,
                dataset_id=identity.dataset_id,
                mode=mode,
                runtime_digest=identity.runtime_digest,
                workflow_id=workflow_id,
                activity_id=activity_id,
                activity_attempt=activity_attempt,
                now=datetime.now(UTC),
            )
            session.commit()
            row = claim.row
            category = FailureCategory(row.failure_category) if row.failure_category else None
            return Claimed(row.id, row.run_no, claim.replay, category)

    def record_vm(self, inp: BuildStageInput, run_id: uuid.UUID, vm_id: str) -> None:
        with self.sessions() as session:
            repo = SandboxRunRepository(session, self._ctx(inp))
            row = repo.get(run_id)
            if row is not None and not row.is_frozen:
                repo.record_vm(row, vm_id)
                session.commit()

    def _put(self, inp: BuildStageInput, data: bytes, content_type: str) -> StoredObject:
        scope = StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id)
        return self.storage.put(data, scope, Kind.TESTS, content_type)

    def finish(
        self,
        inp: BuildStageInput,
        identity: Identity,
        run_id: uuid.UUID,
        *,
        category: FailureCategory | None,
        reason: InfraReason | None,
        exit_code: int | None,
        vm_id: str | None,
        duration_ms: int | None,
        evidence: Evidence | None,
        classified: Classified | None,
        bundle_sha256: str | None,
        dataset_sha256: str | None,
    ) -> None:
        """Close the run. `classified` with a test verdict also writes the LOOP_TESTS gate row."""
        stored: dict[ArtifactKind, StoredObject] = {}
        if evidence is not None:
            if evidence.outputs is not None:
                stored[ArtifactKind.SANDBOX_OUTPUTS] = self._put(inp, evidence.outputs, JSON)
            stored[ArtifactKind.SANDBOX_LOGS] = self._put(inp, evidence.logs, TEXT)
            if evidence.egress:
                lines = "\n".join(e.model_dump_json() for e in evidence.egress) + "\n"
                stored[ArtifactKind.SANDBOX_EGRESS] = self._put(inp, lines.encode(), JSONL)
        with self.sessions() as session:
            ctx = self._ctx(inp)
            artifacts = ArtifactRepository(session, ctx)
            for kind, obj in stored.items():
                artifacts.index(kind, obj, "sbam-worker")
            runs = SandboxRunRepository(session, ctx)
            row = runs.get(run_id)
            if row is None:
                raise LookupError("the sandbox run row is gone")
            truncated = evidence.truncated if evidence is not None else False
            runs.finish(
                row,
                RunOutcome(
                    failure_category=category,
                    infra_reason=reason,
                    exit_code=exit_code,
                    duration_ms=duration_ms,
                    vm_id=vm_id,
                    egress_log_key=_key(stored.get(ArtifactKind.SANDBOX_EGRESS)),
                    outputs_key=_key(stored.get(ArtifactKind.SANDBOX_OUTPUTS)),
                    logs_key=_key(stored.get(ArtifactKind.SANDBOX_LOGS)),
                    truncated=truncated,
                    summary=_summary(classified),
                ),
                datetime.now(UTC),
            )
            if classified is not None and category is not FailureCategory.INFRA_ERROR:
                self._gate_row(session, inp, identity, classified, bundle_sha256, dataset_sha256)
            session.commit()

    def _gate_row(
        self,
        session: Session,
        inp: BuildStageInput,
        identity: Identity,
        classified: Classified,
        bundle_sha256: str | None,
        dataset_sha256: str | None,
    ) -> None:
        """The LOOP_TESTS verdict of this build pass: PASSED, or FAILED with the findings the
        corrector reads. One row per stage key, so a retried pass finds the row it already wrote."""
        findings = [f.model_dump(mode="json", exclude_none=True) for f in classified.findings()]
        GateResultRepository(session, self._ctx(inp)).record_stage(
            {
                "sub_skill_id": inp.sub_skill_id,
                "code_attempt_id": identity.attempt_id,
                "gate": GateName.LOOP_TESTS.value,
                "status": (GateStatus.PASSED if classified.passed else GateStatus.FAILED).value,
                "findings": findings,
                "tool_version": TOOL_VERSION,
                "config_sha256": f"sha256:{dataset_sha256}" if dataset_sha256 else None,
                "build_no": inp.build_no,
                "cycle": inp.cycle,
                "attempt": inp.attempt,
                "bundle_sha256": bundle_sha256,
                "created_by": SYSTEM,
            }
        )


def _key(stored: StoredObject | None) -> str | None:
    return None if stored is None else stored.storage_key


def _summary(classified: Classified | None) -> dict[str, Any] | None:
    """Per case: its name, verdict and category. Never what the code printed or returned."""
    if classified is None:
        return None
    return {
        "cases": [
            {
                "case_id": c.case_id,
                "passed": c.passed,
                "category": c.category.value if c.category else None,
            }
            for c in classified.cases
        ],
        "passed": sum(1 for c in classified.cases if c.passed),
        "failed": sum(1 for c in classified.cases if not c.passed),
        "blocked_egress": len(classified.blocked_egress),
        "notes": list(classified.notes),
    }
