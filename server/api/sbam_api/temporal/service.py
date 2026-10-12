"""Start a workflow and mirror it (E1.7). Every long-running command goes through `start_run`.

Order matters:
1. Size guard: the input must serialise to <= 64 KiB (IDs only), else `PayloadTooLarge`.
2. Start in Temporal with `USE_EXISTING`: a second start of the same workflow id returns the running
   execution instead of starting another (E1.7-T04). Temporal down -> 503, nothing written (T03).
3. Insert the mirror row, or return the one that already exists for that execution.
"""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sbam_contracts.api import ErrorCode
from sbam_contracts.runs import MAX_PAYLOAD_BYTES, Run, RunInput, RunState, SubjectType, TaskQueue
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
)
from temporalio.service import RPCError

from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.models import TemporalRun
from sbam_api.temporal.repository import TemporalRunRepository, to_run

TENANT_ID = SearchAttributeKey.for_keyword("TenantId")
PROJECT_ID = SearchAttributeKey.for_keyword("ProjectId")
SBAM_STAGE = SearchAttributeKey.for_keyword("SbamStage")


class PayloadTooLarge(ApiError):  # noqa: N818 - name from the E1.7 spec
    def __init__(self, size: int) -> None:
        super().__init__(
            413,
            ErrorCode.PAYLOAD_TOO_LARGE,
            "Workflow input too large",
            f"Workflow inputs carry IDs only: {size} bytes is over the {MAX_PAYLOAD_BYTES} limit.",
        )


@dataclass(frozen=True)
class StartSpec:
    workflow: str  # workflow type name, e.g. "PingWorkflow"
    workflow_id: str  # deterministic per subject, see sbam_contracts.runs
    queue: TaskQueue
    subject_type: SubjectType
    subject_id: UUID
    stage: str  # first stage, mirrored as current_stage and SbamStage
    input: RunInput


def check_payload(inp: RunInput) -> None:
    size = len(inp.model_dump_json().encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise PayloadTooLarge(size)


def search_attributes(spec: StartSpec) -> TypedSearchAttributes:
    return TypedSearchAttributes(
        [
            SearchAttributePair(TENANT_ID, str(spec.input.tenant_id)),
            SearchAttributePair(PROJECT_ID, str(spec.input.project_id)),
            SearchAttributePair(SBAM_STAGE, spec.stage),
        ]
    )


async def start_run(
    client: Client,
    sessions: sessionmaker[Session],
    spec: StartSpec,
    *,
    with_search_attributes: bool = True,
) -> Run:
    check_payload(spec.input)
    try:
        handle = await client.start_workflow(
            spec.workflow,
            spec.input,
            id=spec.workflow_id,
            task_queue=spec.queue.value,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            search_attributes=search_attributes(spec) if with_search_attributes else None,
        )
    except (RPCError, RuntimeError):
        raise OrchestratorUnavailable() from None
    temporal_run_id = handle.result_run_id or handle.first_execution_run_id or ""
    return await asyncio.to_thread(_record, sessions, spec, temporal_run_id)


def _record(sessions: sessionmaker[Session], spec: StartSpec, temporal_run_id: str) -> Run:
    ctx = TenantContext(spec.input.tenant_id, spec.input.project_id)
    with sessions() as session:
        repo = TemporalRunRepository(session, ctx)
        existing = repo.find(spec.workflow_id, temporal_run_id)
        if existing is not None:  # duplicate start: same execution, same run object
            return to_run(existing)
        row = TemporalRun(
            id=spec.input.run_id,
            temporal_workflow_id=spec.workflow_id,
            temporal_run_id=temporal_run_id,
            type=spec.workflow,
            task_queue=spec.queue.value,
            subject_type=spec.subject_type.value,
            subject_id=spec.subject_id,
            state=RunState.RUNNING.value,
            current_stage=spec.stage,
        )
        try:
            repo.add(row)
            session.commit()
        except IntegrityError:  # a concurrent start of the same execution inserted it first
            session.rollback()
            existing = TemporalRunRepository(session, ctx).find(spec.workflow_id, temporal_run_id)
            if existing is None:
                raise
            return to_run(existing)
        return to_run(row)
