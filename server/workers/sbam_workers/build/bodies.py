"""The `generate_bodies` build stage (E11.2): LLM bodies for one Sub-Skill build attempt.

Idempotency (TD §13.4): the stage key `(sub_skill_id, build_no, cycle, attempt)` names one
`code_attempts` row and is also the `llm_calls` replay key, so a Temporal retry after the model
answered reuses that answer (no second charge) and finds or writes the same row.

Registered by `wiring.py` (E11.2b) with the loaders and the worker's `LlmClient`.
"""

import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sbam_api.builds.models import CodeAttempt, CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.repository import TenantContext
from sbam_api.llm.repository import LlmCallRepository
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.pipeline import IdResult
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.codegen.bodies import GeneratedBodies, generate_bodies
from sbam_workers.build.codegen.render import RenderedSkeleton
from sbam_workers.build.codegen.store import LoadedTemplate
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.store import LlmScope

STAGE = "generate_bodies"


@dataclass(frozen=True)
class BodiesInputs:
    """What the earlier stages produced for this build: template, params, skeleton, libraries."""

    template: LoadedTemplate
    params: Mapping[str, Any]
    skeleton: RenderedSkeleton
    modules: Iterable[str]  # top-level modules of the registry-resolved libraries (E9.3)
    manifest_id: uuid.UUID | None = None


def stage_key(inp: BuildStageInput) -> str:
    return f"{STAGE}:{inp.sub_skill_id}:{inp.build_no}:{inp.cycle}:{inp.attempt}"


def stage_scope(inp: BuildStageInput, temporal_workflow_id: str) -> LlmScope:
    """The replay key is the stage, not the Temporal attempt: a crash after the model answered
    must find that answer on the retry, so `attempt` stays 1."""
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=temporal_workflow_id,
        activity_id=stage_key(inp),
    )


class SqlAttemptStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def _ctx(self, inp: BuildStageInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def find(self, inp: BuildStageInput) -> tuple[uuid.UUID, str] | None:
        with self.sessions() as session:
            row = CodeAttemptRepository(session, self._ctx(inp)).find(
                inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt
            )
            return None if row is None else (row.id, row.status)

    def record(
        self, inp: BuildStageInput, scope: LlmScope, given: BodiesInputs, out: GeneratedBodies
    ) -> uuid.UUID:
        with self.sessions() as session:
            calls = LlmCallRepository(session, self._ctx(inp)).for_activity(
                scope.temporal_workflow_id, scope.activity_id
            )
            row: CodeAttempt = CodeAttemptRepository(session, self._ctx(inp)).record(
                {
                    "workflow_id": inp.workflow_id,
                    "sub_skill_id": inp.sub_skill_id,
                    "manifest_id": given.manifest_id,
                    "build_no": inp.build_no,
                    "cycle": inp.cycle,
                    "attempt": inp.attempt,
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
                    "created_by": "sbam-worker",
                }
            )
            session.commit()
            return row.id


def precheck_failed(attempt_id: uuid.UUID) -> ApplicationError:
    """GateFailed -> the correction loop; details[0] = ids of the records holding the findings."""
    return ApplicationError(
        "generated bodies failed the pre-check",
        [str(attempt_id)],
        type=GATE_FAILED,
        non_retryable=True,
    )


class GenerateBodies:
    """`generate_bodies` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        inputs: Callable[[BuildStageInput], Awaitable[BodiesInputs]],
        client: LlmClient,
        attempts: SqlAttemptStore,
    ) -> None:
        self.inputs = inputs
        self.client = client
        self.attempts = attempts

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        """LlmTransportError (retryable) and SchemaInvalidAfterRetries (not) propagate before any
        `code_attempts` row exists, so a failed call never leaves a partial attempt."""
        found = self.attempts.find(inp)
        if found is not None:
            attempt_id, status = found
            if status == CodeAttemptStatus.PRECHECK_FAILED:
                raise precheck_failed(attempt_id)
            return IdResult(id=attempt_id)
        given = await self.inputs(inp)
        workflow_id = activity.info().workflow_id
        if workflow_id is None:  # the replay key needs the parent workflow
            raise RuntimeError(f"{STAGE} runs only inside a workflow")
        scope = stage_scope(inp, workflow_id)
        out = await generate_bodies(
            self.client, scope, given.template, given.params, given.skeleton, given.modules
        )
        attempt_id = self.attempts.record(inp, scope, given, out)
        if out.findings:
            activity.logger.info(
                "bodies rejected by the pre-check",
                extra={"codes": sorted({f.code.value for f in out.findings})},
            )
            raise precheck_failed(attempt_id)
        return IdResult(id=attempt_id)
