"""Start, inspect and signal a project's ProjectPipeline (E10.1, E4.1, TD §13.2, §8.4).

* `start_pipeline`: id `pipeline-<project_id>`; one running pipeline per project (409
  PIPELINE_RUNNING), 503 when Temporal is down, mirror row only after a successful start.
* `start_project_pipeline` (E4.1): the endpoint's flow around it. Under the project row lock:
  replay a repeated Idempotency-Key, check the project is ACTIVE and startable and has documents,
  start Temporal, and only then move IDLE -> INGESTING through the state machine (Temporal down
  leaves the state alone). The transition carries the key the workflow's own first activity uses,
  so that activity replays it as a no-op instead of applying it twice.
* `pipeline_status`: the workflow's `status` query (None when the project never started one).
* `signal_pipeline`: for the API endpoints that drive the waits (#90 SOW approval, generate,
  cancel). The caller authorizes the FDE and the project first: signals are never authorization.
  With `expect_stage`, a pipeline that isn't at that wait answers 409 PIPELINE_NOT_WAITING and
  nothing is signalled.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel
from sbam_contracts.api import ErrorCode
from sbam_contracts.pipeline import PROJECT_PIPELINE, PipelineInput, PipelineStage, PipelineStatus
from sbam_contracts.projects import ProjectStatus
from sbam_contracts.runs import Run, SubjectType, TaskQueue, pipeline_workflow_id
from sbam_contracts.state_machines import Actor, MachineName
from sbam_contracts.state_machines import ProjectPipelineState as P
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError, RPCStatusCode

from sbam_api.audit.repository import find_by_key, record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.projects.models import Project
from sbam_api.projects.repository import ProjectRepository
from sbam_api.projects.service import pipeline_running, project_archived
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.temporal.service import StartSpec, start_run

PIPELINE_QUEUE = TaskQueue.INGEST  # where ProjectPipeline's workflow tasks run
QUERY_ATTEMPTS = 4
QUERY_TIMEOUT = timedelta(seconds=5)
QUERY_BACKOFF_SECONDS = 0.25
TRANSIENT = frozenset(  # CANCELLED is how the client reports its own rpc_timeout expiring
    {
        RPCStatusCode.CANCELLED,
        RPCStatusCode.DEADLINE_EXCEEDED,
        RPCStatusCode.UNAVAILABLE,
        RPCStatusCode.NOT_FOUND,
    }
)


TEMPORAL_UNAVAILABLE_DETAIL = (
    "The orchestrator is unavailable; nothing was changed. Try again shortly."
)
# States a start may begin from, with the state-machine event that leaves them (TD §14.2).
START_EVENTS = {P.IDLE: "start_processing", P.INGESTION_FAILED: "retry"}
# The pipeline is alive in these (SOW_REVIEW is its wait for the approval signal).
RUNNING = frozenset(
    {P.INGESTING, P.KNOWLEDGE_BUILDING, P.SOW_GENERATING, P.SOW_REVIEW, P.SOW_APPROVED}
)
PIPELINE_STARTED = "PIPELINE_STARTED"
MAX_IDEMPOTENCY_KEY = 128
KEY_PREFIX = "pipeline-start:"  # keeps a client's key apart from the workflow's own audit keys


class TemporalUnavailable(ApiError):  # noqa: N818 - code name from the E4.1 spec
    def __init__(self) -> None:
        super().__init__(
            503,
            ErrorCode.TEMPORAL_UNAVAILABLE,
            "Orchestrator unavailable",
            TEMPORAL_UNAVAILABLE_DETAIL,
        )


class NoDocuments(ApiError):  # noqa: N818 - name from the E4.1 spec
    def __init__(self) -> None:
        super().__init__(
            409,
            ErrorCode.NO_DOCUMENTS,
            "No documents to process",
            "Upload at least one document before starting the pipeline.",
        )


class PipelineNotStartable(ApiError):  # noqa: N818
    def __init__(self, state: P) -> None:
        super().__init__(
            409,
            ErrorCode.INVALID_TRANSITION,
            "The pipeline cannot be started now",
            f"A pipeline in state {state.value} cannot be started from here.",
        )


class PipelineNotWaiting(ApiError):  # noqa: N818 - name from the E7.1 / E10.1 specs
    def __init__(self, expected: PipelineStage) -> None:
        super().__init__(
            409,
            ErrorCode.PIPELINE_NOT_WAITING,
            "Pipeline is not waiting for this",
            f"The pipeline is not at the {expected.value} step; nothing was sent.",
        )


async def start_pipeline(
    client: Client,
    sessions: sessionmaker[Session],
    pipeline_input: PipelineInput,
    *,
    with_search_attributes: bool = True,
) -> Run:
    resume = pipeline_input.resume
    spec = StartSpec(
        workflow=PROJECT_PIPELINE,
        workflow_id=pipeline_workflow_id(pipeline_input.project_id),
        queue=PIPELINE_QUEUE,
        subject_type=SubjectType.PROJECT,
        subject_id=pipeline_input.project_id,
        stage=(resume.stage if resume is not None else PipelineStage.INGEST).value,
        input=pipeline_input,
    )
    return await start_run(
        client,
        sessions,
        spec,
        with_search_attributes=with_search_attributes,
        conflict=WorkflowIDConflictPolicy.FAIL,
    )


class PipelineBusy(ApiError):  # noqa: N818
    def __init__(self, stage: PipelineStage) -> None:
        super().__init__(
            409,
            ErrorCode.PIPELINE_RUNNING,
            "The pipeline is working",
            f"The pipeline is at the {stage.value} step; approve once it waits or has finished.",
        )


# Where a scope change may replace the run (E7.2): it waits for a person, or it has ended.
RESTARTABLE = frozenset(
    {
        PipelineStage.WAIT_SOW_APPROVAL,
        PipelineStage.WAIT_GENERATE,
        PipelineStage.WAIT_REBUILD,
        PipelineStage.DONE,
        PipelineStage.FAILED,
        PipelineStage.CANCELLED,
    }
)


async def restart_pipeline(
    client: Client,
    sessions: sessionmaker[Session],
    pipeline_input: PipelineInput,
    *,
    with_search_attributes: bool = True,
) -> Run:
    """A scope-change approval (E7.2): replace the project's pipeline run by a new one resuming at
    `pipeline_input.resume`. A run still working (extracting, building, ...) is 409
    PIPELINE_RUNNING and nothing changes; one waiting for a person is terminated first (it changes
    no data: the previous Workflow version stays as it is; the reconciler marks its mirror row)."""
    status = await pipeline_status(client, pipeline_input.project_id)
    if status is not None and status.stage not in RESTARTABLE:
        raise PipelineBusy(status.stage)
    handle = client.get_workflow_handle(pipeline_workflow_id(pipeline_input.project_id))
    try:
        described = await handle.describe()
        if described.status == WorkflowExecutionStatus.RUNNING:
            await handle.terminate(reason="replaced by a scope-change approval (E7.2)")
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise OrchestratorUnavailable() from None
    return await start_pipeline(
        client, sessions, pipeline_input, with_search_attributes=with_search_attributes
    )


async def pipeline_status(
    client: Client, project_id: UUID, *, attempts: int = QUERY_ATTEMPTS
) -> PipelineStatus | None:
    """The `status` query, or None if this project never started a pipeline.

    A query can race a continue-as-new (it reaches the run that is closing) and come back
    DEADLINE_EXCEEDED / UNAVAILABLE / NOT_FOUND for a moment; the next one reaches the new run. So
    those are retried a few times, each with a short timeout, before answering None / 503.
    """
    handle = client.get_workflow_handle(pipeline_workflow_id(project_id))
    for attempt in range(1, attempts + 1):
        try:
            status: PipelineStatus = await handle.query(
                "status", result_type=PipelineStatus, rpc_timeout=QUERY_TIMEOUT
            )
            return status
        except RPCError as exc:
            if exc.status not in TRANSIENT or attempt == attempts:
                if exc.status == RPCStatusCode.NOT_FOUND:
                    return None
                raise OrchestratorUnavailable() from None
            await asyncio.sleep(QUERY_BACKOFF_SECONDS * attempt)
    return None  # attempts < 1


async def signal_pipeline(
    client: Client,
    project_id: UUID,
    signal: str,
    payload: BaseModel,
    *,
    expect_stage: PipelineStage | None = None,
) -> None:
    if expect_stage is not None:
        status = await pipeline_status(client, project_id)
        if status is None or status.stage is not expect_stage:
            raise PipelineNotWaiting(expect_stage)
    try:
        await client.get_workflow_handle(pipeline_workflow_id(project_id)).signal(signal, payload)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            raise PipelineNotWaiting(expect_stage or PipelineStage.INGEST) from None
        raise OrchestratorUnavailable() from None


def _prior_start(session: Session, user: CurrentUser, project_id: UUID, key: str) -> Run | None:
    """The run this Idempotency-Key already started, if any."""
    ctx = user.context(project_id)
    prior = find_by_key(session, ctx.tenant_id, "project", str(project_id), KEY_PREFIX + key)
    if prior is None or prior.action != PIPELINE_STARTED or not prior.after:
        return None
    row = TemporalRunRepository(session, ctx).get(UUID(prior.after["run_id"]))
    return to_run(row) if row is not None else None


def _check_startable(
    session: Session, user: CurrentUser, project_id: UUID
) -> tuple[Project, list[UUID]]:
    """Lock the project row and check it can start. Returns it with its active document ids."""
    ctx = user.context(project_id)
    project = ProjectRepository(session, ctx).get_for_update(project_id)
    if project is None:
        raise not_found()
    if project.status == ProjectStatus.ARCHIVED.value:
        raise project_archived()
    state = P(project.state)
    if state in RUNNING:
        raise pipeline_running()
    if state not in START_EVENTS:
        raise PipelineNotStartable(state)
    documents = DocumentVersionRepository(session, ctx).active_ids()
    if not documents:
        raise NoDocuments()
    return project, documents


def _commit_start(
    session: Session,
    user: CurrentUser,
    project: Project,
    run: Run,
    key: str | None,
    correlation_id: str,
) -> None:
    ctx = user.context(project.id)
    before = project.state
    event = START_EVENTS[P(before)]
    apply_transition(
        session,
        ctx,
        TransitionCommand(
            model=Project,
            entity_id=project.id,
            event=event,
            actor=Actor.FDE,
            actor_id=str(user.user_id),
            correlation_id=correlation_id,
            # The key ProjectPipeline's first activity sends (`_stage_ingest` always sends
            # start_processing), so that activity finds this transition applied and does nothing,
            # also when this one is a `retry` out of INGESTION_FAILED.
            idempotency_key=f"{run.temporal_run_id}:{MachineName.PROJECT_PIPELINE.value}:start_processing:0",
        ),
    )
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=PIPELINE_STARTED,
        entity_type="project",
        entity_id=str(project.id),
        correlation_id=correlation_id,
        before={"state": before},
        after={"run_id": str(run.id), "temporal_workflow_id": run.temporal_workflow_id},
        idempotency_key=KEY_PREFIX + key if key else None,
    )
    session.commit()


async def start_project_pipeline(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: UUID,
    *,
    idempotency_key: str | None = None,
    with_search_attributes: bool = True,
) -> Run:
    """POST P/pipeline/start. The project row stays locked from the checks to the commit, so an
    archive or a second start waits instead of racing; Temporal's FAIL conflict policy is what
    guarantees one execution when two requests get past the checks on different connections."""
    ctx = user.context(project_id)
    correlation_id = correlation_id_var.get() or str(uuid7())
    with sessions() as session:
        try:
            if idempotency_key:  # a retry: the same run, whatever state the pipeline is in now
                replay = await asyncio.to_thread(
                    _prior_start, session, user, project_id, idempotency_key
                )
                if replay is not None:
                    return replay
            project, documents = await asyncio.to_thread(
                _check_startable, session, user, project_id
            )
            try:
                client = await get_client()
            except OrchestratorUnavailable:
                raise TemporalUnavailable() from None
            pipeline_input = PipelineInput(
                tenant_id=ctx.tenant_id,
                project_id=project_id,
                run_id=uuid7(),
                correlation_id=correlation_id,
                actor_id=str(user.user_id),
                document_version_ids=documents,  # from the database, never from the request
                search_attributes=with_search_attributes,
            )
            try:
                run = await start_pipeline(
                    client, sessions, pipeline_input, with_search_attributes=with_search_attributes
                )
            except OrchestratorUnavailable:
                raise TemporalUnavailable() from None
            await asyncio.to_thread(
                _commit_start, session, user, project, run, idempotency_key, correlation_id
            )
            return run
        except BaseException:
            await asyncio.to_thread(session.rollback)
            raise
