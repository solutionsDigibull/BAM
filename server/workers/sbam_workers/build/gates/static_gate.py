"""The `static_gate` build stage (E12.4): Ruff, Black, MyPy --strict, Bandit and ClamAV on a bundle.

It judges the stored bundle of the code attempt (`stored_bundle`: found, re-read and verified
against the hashes recorded at assembly; a mismatch is `BUNDLE_TAMPERED`) and writes one
`gate_results` row per gate with the tool version and the sha256 of its pinned config. Blocking:
any Ruff, Black or MyPy finding, a Bandit HIGH (MEDIUM and LOW are reported on a passing verdict)
and any ClamAV detection. A blocking failure raises `GateFailed`, details[0] = the attempt id, so
`SubSkillBuild` corrects it from the stored findings.

A tool that gives no verdict (timeout, crash, clamd unreachable, signatures older than 24 hours) is
`STATIC_GATE_INFRA`: retryable, never a correction; after the retries the build stops as a platform
defect for an operator. Nothing is recorded for a gate that gave no verdict. A ClamAV detection
also copies the bundle to quarantine storage and writes an audit row, and is logged as an alert.
"""

import asyncio
import io
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

from sbam_api.audit.repository import record
from sbam_api.scan.clamd import ClamdClient, ClamdError, require_fresh
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus, Severity
from sbam_contracts.pipeline import IdResult
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.gates.errors import STATIC_GATE_INFRA
from sbam_workers.build.gates.static_tools import TOOLS, ToolResult, finding
from sbam_workers.build.gates.stored_bundle import (
    StoredBundle,
    Verdict,
    fail,
    find_assembled,
    read_verified,
    record_verdicts,
)
from sbam_workers.build.gates.toolchain import ToolFailure

STAGE = "static_gate"
GATES = (GateName.RUFF, GateName.BLACK, GateName.MYPY, GateName.BANDIT, GateName.CLAMAV)
MAX_SIGNATURE_AGE: Final = timedelta(hours=24)
QUARANTINE_MIME: Final = "application/x-tar"
SYSTEM_ACTOR: Final = "SYSTEM"
log = logging.getLogger("sbam_workers.build.static_gate")


def _infra(message: str) -> ApplicationError:
    """Retryable on purpose: the tool or clamd may be back on the next attempt."""
    return ApplicationError(message, type=STATIC_GATE_INFRA)


class StaticGate:
    """`static_gate` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        storage: StorageBackend,
        sessions: sessionmaker[Session],
        clamd: ClamdClient,
        tools: tuple[Callable[[Mapping[str, bytes]], ToolResult], ...] = TOOLS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.storage = storage
        self.sessions = sessions
        self.clamd = clamd
        self.tools = tools
        self.clock = clock

    def _scan(self, bundle: StoredBundle) -> tuple[Verdict, list[str]]:
        """ClamAV over every file of the bundle; the second item names the signatures found."""
        try:
            version = self.clamd.version()
            require_fresh(version, self.clock(), MAX_SIGNATURE_AGE)
            found: list[dict[str, object]] = []
            signatures: list[str] = []
            for name in sorted(bundle.files):
                verdict = self.clamd.scan(io.BytesIO(bundle.files[name]))
                if verdict.infected:
                    signatures.append(verdict.signature or "unknown")
                    found.append(
                        finding(
                            GateName.CLAMAV,
                            verdict.signature or "MALWARE",
                            name,
                            None,
                            Severity.HIGH,
                            "malware signature detected",
                        )
                    )
        except ClamdError as exc:  # includes SignatureStaleError
            raise _infra(str(exc)) from None
        status = GateStatus.FAILED if found else GateStatus.PASSED
        return Verdict(GateName.CLAMAV, status, found, version.raw[:100], "clamav"), signatures

    def _quarantine(
        self, inp: BuildStageInput, bundle: StoredBundle, signatures: list[str]
    ) -> None:
        scope = StorageScope(inp.tenant_id, inp.project_id, inp.workflow_id)
        stored = self.storage.put(io.BytesIO(bundle.tar), scope, Kind.QUARANTINE, QUARANTINE_MIME)
        with self.sessions() as session:
            record(
                session,
                tenant_id=inp.tenant_id,
                actor_id=SYSTEM_ACTOR,
                action="BUNDLE_QUARANTINED",
                entity_type="code_attempt",
                entity_id=str(bundle.attempt_id),
                after={
                    "signatures": sorted(set(signatures)),
                    "quarantine_key": stored.storage_key,
                    "bundle_sha256": bundle.bundle_sha,
                },
                correlation_id=inp.correlation_id,
            )
            session.commit()
        log.error(
            "malware detected in generated code; bundle quarantined",
            extra={
                "alert": "operator",
                "signatures": sorted(set(signatures)),
                "tenant_id": str(inp.tenant_id),
                "project_id": str(inp.project_id),
                "workflow_id": str(inp.workflow_id),
                "sub_skill_id": str(inp.sub_skill_id),
                "correlation_id": inp.correlation_id,
            },
        )

    def _judge(self, bundle: StoredBundle) -> tuple[list[Verdict], list[str]]:
        verdicts: list[Verdict] = []
        try:
            for tool in self.tools:
                r = tool(bundle.files)
                verdicts.append(
                    Verdict(r.gate, r.status, r.findings, r.tool_version, r.config_sha256)
                )
        except ToolFailure as exc:
            raise _infra(str(exc)) from None
        scan, signatures = self._scan(bundle)
        return [*verdicts, scan], signatures

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        # the tools and clamd block: keep them off the worker's event loop
        return await asyncio.to_thread(self.execute, inp)

    def execute(self, inp: BuildStageInput) -> IdResult:
        assembled = find_assembled(self.sessions, inp)
        bundle = read_verified(self.storage, assembled)
        verdicts, signatures = self._judge(bundle)
        if signatures:  # before the verdict is recorded: a retry still quarantines the bytes
            self._quarantine(inp, bundle, signatures)
        recorded = record_verdicts(self.sessions, inp, bundle, verdicts)
        if any(s is not GateStatus.PASSED for s in recorded):
            bad = [
                v.gate.value
                for v, s in zip(verdicts, recorded, strict=True)
                if s is not GateStatus.PASSED
            ]
            activity.logger.info(
                "generated code failed the static gate",
                extra={
                    "gates": bad,
                    "tenant_id": str(inp.tenant_id),
                    "project_id": str(inp.project_id),
                    "workflow_id": str(inp.workflow_id),
                    "sub_skill_id": str(inp.sub_skill_id),
                    "correlation_id": inp.correlation_id,
                },
            )
            raise fail("generated code failed the static gate", assembled.attempt_id, GATE_FAILED)
        return IdResult(id=assembled.attempt_id)
