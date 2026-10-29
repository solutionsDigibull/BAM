"""The `correct` build stage (E11.4, TD §12.4): findings -> `sbam-corrector` -> the next attempt.

The workflow calls `correct` with the failed pass's `attempt` and the ids of the records holding
its findings (`code_attempts` rows; other ids are ignored). The correction is written as the
`code_attempts` row of `attempt + 1`, which the next pass assembles without generating new bodies:

* BODIES_GENERATED: the corrector's bodies (or the unchanged ones) passed the pre-check and any
  ISATVON patch was accepted;
* PRECHECK_FAILED: a rejected patch or rejected bodies. The row is still the attempt (the
  correction was spent); `assemble` raises GateFailed for it, so the loop moves on or escalates.

An accepted ISATVON patch is written to the audit log (actor `sbam-corrector`) in the same
transaction as the row, with the Sub-Skill before and after, both hashes, the patch and the
rationale. A second correction in the same cycle patches the first correction's result, read back
from that audit row. Until Sub-Skills are persisted (#93) and re-manifested (#101), that audit row
is where the patched ISATVON lives; the new loop spec's hash is recorded with it.

Idempotency (TD §13.4): the stage key names the `attempt + 1` row and the `llm_calls` replay key,
so a retry after the model answered reuses the answer (no second charge) and finds the row. LLM
errors (transport, schema) propagate before anything is written.

Registered by `wiring.py` (E11.2b) with the loaders and the worker's `LlmClient`.
"""

import hashlib
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sbam_api.audit import repository as audit
from sbam_api.builds.models import CodeAttempt, CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.llm.repository import LlmCallRepository
from sbam_contracts.build import (
    MAX_CORRECTIONS,
    BuildStageInput,
    CorrectionInput,
    CorrectionResult,
    IsatvonPatchClass,
)
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon14 import DEFAULT_LIBRARY_REGISTRY
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.codegen.bodies import CodeBodies
from sbam_workers.build.codegen.correction import (
    ALIAS,
    PATCH_ROOT,
    Correction,
    PatchOp,
    bodies_diff,
    classify_patch,
    correct_code,
    intake,
)
from sbam_workers.build.codegen.render import RenderedSkeleton
from sbam_workers.build.codegen.store import LoadedTemplate
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.store import LlmScope

STAGE = "correct"
INPUT_MISMATCH = "CORRECTION_INPUT_MISMATCH"
AUDIT_ACTION = "isatvon_corrected"
AUDIT_ENTITY = "sub_skill"


@dataclass(frozen=True)
class CorrectionInputs:
    """What the earlier stages produced for this build, plus the Sub-Skill and the harness trace."""

    template: LoadedTemplate
    params: Mapping[str, Any]
    skeleton: RenderedSkeleton
    modules: Iterable[str]  # top-level modules of the registry-resolved libraries (E9.3)
    sub_skill: Mapping[str, Any]  # the structured Sub-Skill (skill_metadata + isatvon_definition)
    manifest_id: uuid.UUID | None = None
    trace: str = ""  # the failing sandbox run's harness trace (tests_failed), if any
    library_registry: Iterable[str] = field(default=DEFAULT_LIBRARY_REGISTRY)


@dataclass(frozen=True)
class Failing:
    """The failed pass the corrector works from."""

    bodies: CodeBodies
    findings: list[dict[str, Any]]
    sub_skill: Mapping[str, Any] | None  # an earlier correction's patched Sub-Skill, if any
    previous: dict[str, Any] | None  # attempt 2: what correction 1 changed and why


def stage_key(inp: BuildStageInput, attempt: int) -> str:
    return f"{STAGE}:{inp.sub_skill_id}:{inp.build_no}:{inp.cycle}:{attempt}"


def stage_scope(inp: CorrectionInput, temporal_workflow_id: str) -> LlmScope:
    """Keyed by the attempt the correction writes; a Temporal retry finds the same answer."""
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=temporal_workflow_id,
        activity_id=stage_key(inp, inp.attempt + 1),
    )


def _mismatch(message: str) -> ApplicationError:
    return ApplicationError(message, type=INPUT_MISMATCH, non_retryable=True)


class SqlCorrectionStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def _ctx(self, inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def find(self, inp: CorrectionInput) -> uuid.UUID | None:
        """The row this correction already wrote, if any."""
        with self.sessions() as session:
            row = CodeAttemptRepository(session, self._ctx(inp)).find(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt + 1
            )
            return None if row is None else row.id

    def result(self, inp: CorrectionInput, attempt_id: uuid.UUID) -> CorrectionResult:
        """The result of a correction already written (a retried activity). What it did to the
        ISATVON is in its audit row; no row means no accepted patch (bodies only, or a rejected
        patch). Rows written before the class was stored are classified from their operations."""
        with self.sessions() as session:
            row = audit.find_by_key(
                session,
                inp.tenant_id,
                AUDIT_ENTITY,
                str(inp.sub_skill_id),
                stage_key(inp, inp.attempt + 1),
            )
            after = None if row is None else row.after
        if not after:
            return CorrectionResult(id=attempt_id)
        if "patch_class" in after:
            kind, paths = IsatvonPatchClass(after["patch_class"]), tuple(after["changed_paths"])
        else:
            kind, paths = classify_patch([PatchOp.model_validate(op) for op in after["patch"]])
        return CorrectionResult(id=attempt_id, isatvon_patch=kind, changed_paths=list(paths))

    def failing(self, inp: CorrectionInput) -> Failing:
        with self.sessions() as session:
            repo = CodeAttemptRepository(session, self._ctx(inp))
            row = repo.find(inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt)
            if row is None:  # the failed pass always has its row; a missing one is a wiring bug
                raise _mismatch(
                    f"no code_attempts row for {inp.sub_skill_id} attempt {inp.attempt}"
                )
            findings: list[dict[str, Any]] = []
            for finding_id in dict.fromkeys(inp.finding_ids):
                source = repo.get(finding_id)  # other tenants' and unknown ids read as None
                if source is not None and source.sub_skill_id == inp.sub_skill_id:
                    findings.extend(source.findings)
            sub_skill: Mapping[str, Any] | None = None
            previous: dict[str, Any] | None = None
            if inp.attempt > 0:
                earlier = audit.find_by_key(
                    session,
                    inp.tenant_id,
                    AUDIT_ENTITY,
                    str(inp.sub_skill_id),
                    stage_key(inp, inp.attempt),
                )
                if earlier is not None and earlier.after is not None:
                    sub_skill = earlier.after["sub_skill"]
                first = repo.find(inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt - 1)
                if first is not None:
                    previous = {
                        "attempt": inp.attempt,
                        "bodies_diff": bodies_diff(first.bodies, row.bodies),
                        "rationale": row.correction_reason or "",
                        "findings_it_answered": intake(first.findings)["findings"],
                    }
            return Failing(CodeBodies.model_validate(row.bodies), findings, sub_skill, previous)

    def record(
        self,
        inp: CorrectionInput,
        scope: LlmScope,
        given: CorrectionInputs,
        before: Mapping[str, Any],
        out: Correction,
    ) -> uuid.UUID:
        """The `attempt + 1` row and, for an accepted patch, its audit row: one transaction."""
        with self.sessions() as session:
            ctx = self._ctx(inp)
            calls = LlmCallRepository(session, ctx).for_activity(
                scope.temporal_workflow_id, scope.activity_id
            )
            row: CodeAttempt = CodeAttemptRepository(session, ctx).record(
                {
                    "workflow_id": inp.workflow_id,
                    "sub_skill_id": inp.sub_skill_id,
                    "manifest_id": given.manifest_id,
                    "build_no": inp.build_no,
                    "cycle": inp.cycle,
                    "attempt": inp.attempt + 1,
                    "template_name": given.template.name,
                    "template_version": given.template.version,
                    "template_sha256": given.template.sha256,
                    "status": (
                        CodeAttemptStatus.PRECHECK_FAILED
                        if out.findings
                        else CodeAttemptStatus.BODIES_GENERATED
                    ),
                    "bodies": out.bodies.model_dump(),
                    "llm_call_ids": [str(call.id) for call in calls],
                    "trace_id": out.trace_id,
                    "findings": [finding.as_dict() for finding in out.findings],
                    "correction_reason": f"[{inp.failed_gate}] {out.output.rationale}",
                    "created_by": ALIAS,
                }
            )
            if out.sub_skill is not None and out.loop_spec is not None:
                key = scope.activity_id
                existing = audit.find_by_key(
                    session, inp.tenant_id, AUDIT_ENTITY, str(inp.sub_skill_id), key
                )
                if existing is None:
                    audit.record(
                        session,
                        tenant_id=inp.tenant_id,
                        actor_id=ALIAS,
                        action=AUDIT_ACTION,
                        entity_type=AUDIT_ENTITY,
                        entity_id=str(inp.sub_skill_id),
                        correlation_id=inp.correlation_id,
                        before={
                            "sub_skill": dict(before),
                            "isatvon_sha256": sha256_of(before[PATCH_ROOT]),
                        },
                        after={
                            "sub_skill": dict(out.sub_skill),
                            "isatvon_sha256": out.isatvon_sha256,
                            "loop_spec_sha256": hashlib.sha256(out.loop_spec).hexdigest(),
                            "patch": [
                                op.model_dump() for op in out.output.isatvon_patch or []
                            ],
                            "patch_class": out.patch_class.value,
                            "changed_paths": list(out.changed_paths),
                            "rationale": out.output.rationale,
                            "failed_gate": inp.failed_gate,
                            "attempt": inp.attempt + 1,
                            "code_attempt_id": str(row.id),
                        },
                        idempotency_key=key,
                    )
            session.commit()
            return row.id


class Correct:
    """`correct` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        inputs: Callable[[CorrectionInput], Awaitable[CorrectionInputs]],
        client: LlmClient,
        attempts: SqlCorrectionStore,
    ) -> None:
        self.inputs = inputs
        self.client = client
        self.attempts = attempts

    @activity.defn(name=STAGE)
    async def run(self, inp: CorrectionInput) -> CorrectionResult:
        if inp.attempt >= MAX_CORRECTIONS:  # the workflow escalates before this; fail closed
            raise _mismatch(f"attempt {inp.attempt} has no correction left")
        found = self.attempts.find(inp)
        if found is not None:
            return self.attempts.result(inp, found)
        failing = self.attempts.failing(inp)
        given = await self.inputs(inp)
        workflow_id = activity.info().workflow_id
        if workflow_id is None:  # the replay key needs the parent workflow
            raise RuntimeError(f"{STAGE} runs only inside a workflow")
        scope = stage_scope(inp, workflow_id)
        sub_skill = failing.sub_skill or given.sub_skill
        out = await correct_code(
            self.client,
            scope,
            sub_skill=sub_skill,
            current=failing.bodies,
            template=given.template,
            params=given.params,
            skeleton=given.skeleton,
            modules=given.modules,
            findings=failing.findings,
            trace=given.trace,
            previous=failing.previous,
            library_registry=given.library_registry,
        )
        attempt_id = self.attempts.record(inp, scope, given, sub_skill, out)
        if out.findings:
            activity.logger.info(
                "correction rejected",
                extra={"codes": sorted({f.as_dict()["code"] for f in out.findings})},
            )
        return CorrectionResult(
            id=attempt_id,
            isatvon_patch=out.patch_class,
            changed_paths=list(out.changed_paths),
        )

