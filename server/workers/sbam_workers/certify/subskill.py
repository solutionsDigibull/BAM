"""The real `certify_sub_skill` stage (E14.1): certify one assembled, gated and tested build.

Registered under the stub's name and queue with the same Pydantic in/out (`BuildStageInput` ->
`IdResult`), so `SubSkillBuild` does not change. The id returned is the `certifications` row. The
workflow applies `tests_passed` (CERTIFIED) after this activity returns; nothing else sets the
manifest's status, lifecycle or certification blocks, and a model never sees them.

What it does, in one transaction:
  1. loads the evidence of the build's stage key: gate results, the test run and the sandbox run it
     judged, the dataset and suite as they are now, and the SOW version the manifest was built from;
  2. asks `checklist.evaluate`; anything missing or mismatched raises the non-retryable
     `CertificationBlocked` and writes nothing;
  3. seals the manifest as APPROVED (the deterministic hash is recomputed over the approved
     document; the evidence and certification blocks are outside it) and freezes its row;
  4. inserts the certificate.

A retry finds the certificate of the same (Sub-Skill, bundle hash) and returns it: no second row,
no second manifest write. Messages and logs carry ids, gate names and reasons, never code.
"""

import asyncio
import copy
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.certifications.models import CertificationLevel
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import GateResultRepository, SubSkillManifestRepository
from sbam_api.sandbox.repository import SandboxRunRepository
from sbam_api.sow.repository import SowVersionRepository
from sbam_api.testgen.repository import CaseRepository, DatasetRepository
from sbam_api.testruns.repository import TestRunRepository
from sbam_contracts.build import BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus, Severity
from sbam_contracts.manifest import SkillManifest
from sbam_contracts.pipeline import IdResult
from sbam_contracts.promptfoo import TestRunState, suite_sha256
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sandbox import SandboxMode
from sbam_contracts.testgen import CaseKind
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.certify.checklist import (
    Evidence,
    GateEvidence,
    SandboxEvidence,
    TestEvidence,
    Verdict,
    evaluate,
)
from sbam_workers.registry import activity_for
from sbam_workers.test.promptfoo_suite import load_suite

IsatvonOf = Callable[[BuildStageInput], Awaitable[Mapping[str, Any]]]
BLOCKED = "CertificationBlocked"
CREATED_BY = "worker-certify"


def blocked(reasons: tuple[str, ...]) -> ApplicationError:
    return ApplicationError(
        f"certification blocked: {', '.join(reasons)}", type=BLOCKED, non_retryable=True
    )


def _digest(value: str) -> str:
    return value.removeprefix("sha256:")


def _passed_kinds(
    results: list[dict[str, Any]], kinds: Mapping[str, CaseKind]
) -> frozenset[CaseKind]:
    """The kinds of the cases whose every assertion held."""
    failed = {r["case"] for r in results if not r["passed"]}
    return frozenset(kind for name, kind in kinds.items() if name not in failed)


def approved_manifest(
    manifest: Mapping[str, Any], evidence: Mapping[str, Any], certified_at: datetime
) -> SkillManifest:
    """The manifest as certification leaves it: APPROVED, immutable, with its evidence and
    certification blocks, and a deterministic hash recomputed over the approved document."""
    stamp = certified_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    data = copy.deepcopy(dict(manifest))
    data["skill_metadata"]["status"] = "APPROVED"
    data["skill_metadata"]["updated_at"] = stamp
    data["governance"]["lifecycle"] = {
        "state": "APPROVED",
        "approval_required_for_change": True,
        "immutable_after_approval": True,
    }
    record = data["sbam_build_record"]
    record["evidence"] = dict(evidence)
    record["certification"] = {"state": "CERTIFIED", "certified_at": stamp, "reused_from": None}
    return SkillManifest.sealed(data)


class CertifySubSkill:
    """`certify_sub_skill` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        isatvon_of: IsatvonOf,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessions
        self.isatvon_of = isatvon_of
        self.clock = clock

    def _load(
        self,
        session: Session,
        ctx: TenantContext,
        inp: BuildStageInput,
        current_suite: str,
    ) -> tuple[Evidence, Any, dict[str, Any], str | None] | ApplicationError:
        attempts = CodeAttemptRepository(session, ctx)
        attempt = attempts.find(inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt)
        if attempt is None or attempt.manifest_id is None or not attempt.bundle_sha256:
            return blocked(("MISSING_EVIDENCE:CODE_ATTEMPT",))
        manifest_row = SubSkillManifestRepository(session, ctx).get(attempt.manifest_id)
        if manifest_row is None:
            return blocked(("MISSING_EVIDENCE:MANIFEST",))
        gates = [
            GateEvidence(
                gate=GateName(g.gate),
                status=GateStatus(g.status),
                # keyed to the stage; a gate that ran before the attempt existed names none
                code_attempt_id=str(g.code_attempt_id or attempt.id),
                bundle_sha256=g.bundle_sha256,
                severities=[
                    Severity(f["severity"]) if f.get("severity") else None for f in g.findings
                ],
            )
            for g in GateResultRepository(session, ctx).of_stage(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
        ]
        run = TestRunRepository(session, ctx).of_code_attempt(attempt.id)
        sandbox = (
            None if run is None else SandboxRunRepository(session, ctx).get(run.sandbox_run_id)
        )
        dataset = DatasetRepository(session, ctx).latest(inp.sub_skill_id)
        kinds = (
            {}
            if dataset is None
            else {
                c.name: CaseKind(c.kind)
                for c in CaseRepository(session, ctx).of_dataset(dataset.id)
            }
        )
        sandbox_dataset = (
            None if sandbox is None else DatasetRepository(session, ctx).get(sandbox.dataset_id)
        )
        test = (
            None
            if run is None
            else TestEvidence(
                id=str(run.id),
                sandbox_run_id=str(run.sandbox_run_id),
                code_attempt_id=str(run.code_attempt_id),
                state=TestRunState(run.state),
                total=run.total,
                passed=run.passed,
                failed=run.failed,
                suite_sha256=run.suite_sha256,
                passed_kinds=_passed_kinds(run.results, kinds),
            )
        )
        evidence = Evidence(
            code_attempt_id=str(attempt.id),
            bundle_sha256=attempt.bundle_sha256,
            gates=gates,
            sandbox=(
                None
                if sandbox is None or sandbox_dataset is None
                else SandboxEvidence(
                    id=str(sandbox.id),
                    code_attempt_id=str(sandbox.code_attempt_id),
                    mode=SandboxMode(sandbox.mode),
                    runtime_digest=sandbox.runtime_digest,
                    dataset_sha256=sandbox_dataset.sha256,
                )
            ),
            test=test,
            current_dataset_sha256="" if dataset is None else dataset.sha256,
            current_suite_sha256=current_suite,
            current_runtime_digest=manifest_row.runtime_digest,
        )
        return (
            evidence,
            manifest_row,
            dict(manifest_row.manifest),
            _sow_problem(session, ctx, manifest_row.manifest),
        )

    def _certify(self, inp: BuildStageInput, isatvon: Mapping[str, Any]) -> uuid.UUID:
        ctx = TenantContext(inp.tenant_id, inp.project_id)
        try:
            suite, _ = load_suite(self.sessions, inp, isatvon)
            current_suite = suite_sha256(suite)
        except ApplicationError:
            current_suite = ""  # no suite to compare to: the checklist reports SUITE_CHANGED
        with self.sessions() as session:
            attempt = CodeAttemptRepository(session, ctx).find(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
            if attempt is not None and attempt.bundle_sha256:
                done = CertificationRepository(session, ctx).find(
                    CertificationLevel.SUB_SKILL,
                    inp.sub_skill_id,
                    "sha256:" + attempt.bundle_sha256,
                )
                if done is not None:  # a retried activity: the certificate stands
                    return done.id
            loaded = self._load(session, ctx, inp, current_suite)
            if isinstance(loaded, ApplicationError):
                raise loaded
            evidence, manifest_row, manifest, sow_problem = loaded
            verdict: Verdict = evaluate(evidence)
            reasons = verdict.reasons + (() if sow_problem is None else (sow_problem,))
            if reasons:
                raise blocked(reasons)
            certified_at = self.clock()
            if evidence.test is None or evidence.sandbox is None:  # the checklist said otherwise
                raise blocked(("MISSING_EVIDENCE:TEST_RUN",))
            record = {
                "code_attempt_id": evidence.code_attempt_id,
                "bundle_sha256": evidence.bundle_sha256,
                "gates": [{"gate": g.gate.value, "status": g.status.value} for g in evidence.gates],
                "test_run_id": evidence.test.id,
                "sandbox_run_id": evidence.sandbox.id,
                "passed_cases": evidence.test.passed,
            }
            try:
                approved = approved_manifest(manifest, record, certified_at)
            except (ValidationError, ValueError, KeyError):
                raise blocked(("MANIFEST_INVALID",)) from None
            manifest_row.manifest = approved.dump()
            manifest_row.sha256 = approved.governance.deterministic_hash
            manifest_row.is_frozen = True
            certificate = CertificationRepository(session, ctx).record(
                {
                    "level": CertificationLevel.SUB_SKILL.value,
                    "subject_id": inp.sub_skill_id,
                    "content_sha256": "sha256:" + evidence.bundle_sha256,
                    "evidence": {
                        **record,
                        "dataset_sha256": evidence.current_dataset_sha256,
                        "suite_sha256": evidence.test.suite_sha256,
                        "manifest_sha256": manifest_row.sha256,
                    },
                    "certified_at": certified_at,
                    "created_by": CREATED_BY,
                }
            )
            session.commit()
            return certificate.id

    @activity.defn(name="certify_sub_skill")
    async def run(self, inp: BuildStageInput) -> IdResult:
        isatvon = await self.isatvon_of(inp)
        return IdResult(id=await asyncio.to_thread(self._certify, inp, isatvon))


def _sow_problem(session: Session, ctx: TenantContext, manifest: Mapping[str, Any]) -> str | None:
    """The SOW the manifest was built from must still be APPROVED on the hash it records."""
    provenance = manifest["sbam_build_record"]["provenance"]
    try:
        version_id = uuid.UUID(provenance["sow_version_id"])
    except ValueError:
        return "SOW_NOT_APPROVED"
    version = SowVersionRepository(session, ctx).get(version_id)
    if version is None or version.state != "APPROVED" or version.approved_at is None:
        return "SOW_NOT_APPROVED"
    if _digest(version.sha256 or "") != _digest(provenance["sow_sha256"]):
        return "SOW_HASH_MISMATCH"
    return None


def _stage() -> CertifySubSkill:
    from sbam_workers.build.wiring import SqlBuildInputs

    sessions = mirror.sessions()
    return CertifySubSkill(sessions, SqlBuildInputs(sessions).tests_sub_skill)


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="certify_sub_skill")
async def certify_sub_skill(inp: BuildStageInput) -> IdResult:
    return await _stage().run(inp)
