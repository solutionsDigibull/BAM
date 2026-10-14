"""Start, inspect and signal a project's ProjectPipeline (E10.1, TD §13.2).

* `start_pipeline`: id `pipeline-<project_id>`; one running pipeline per project (409
  PIPELINE_RUNNING), 503 when Temporal is down, mirror row only after a successful start.
* `pipeline_status`: the workflow's `status` query (None when the project never started one).
* `signal_pipeline`: for the API endpoints that drive the waits (#90 SOW approval, generate,
  cancel). The caller authorizes the FDE and the project first: signals are never authorization.
  With `expect_stage`, a pipeline that isn't at that wait answers 409 PIPELINE_NOT_WAITING and
  nothing is signalled.
"""

from uuid import UUID

from pydantic import BaseModel
from sbam_contracts.api import ErrorCode
from sbam_contracts.pipeline import PROJECT_PIPELINE, PipelineInput, PipelineStage, PipelineStatus
from sbam_contracts.runs import Run, SubjectType, TaskQueue, pipeline_workflow_id
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError, RPCStatusCode

from sbam_api.core.conventions import ApiError
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.service import StartSpec, start_run

PIPELINE_QUEUE = TaskQueue.INGEST  # where ProjectPipeline's workflow tasks run


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
    spec = StartSpec(
        workflow=PROJECT_PIPELINE,
        workflow_id=pipeline_workflow_id(pipeline_input.project_id),
        queue=PIPELINE_QUEUE,
        subject_type=SubjectType.PROJECT,
        subject_id=pipeline_input.project_id,
        stage=PipelineStage.INGEST.value,
        input=pipeline_input,
    )
    return await start_run(
        client,
        sessions,
        spec,
        with_search_attributes=with_search_attributes,
        conflict=WorkflowIDConflictPolicy.FAIL,
    )


async def pipeline_status(client: Client, project_id: UUID) -> PipelineStatus | None:
    handle = client.get_workflow_handle(pipeline_workflow_id(project_id))
    try:
        status: PipelineStatus = await handle.query("status", result_type=PipelineStatus)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return None
        raise OrchestratorUnavailable() from None
    return status


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
