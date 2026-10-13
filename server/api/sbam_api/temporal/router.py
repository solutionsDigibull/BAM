"""Run endpoints (TD §8.1): poll a run, and the sample long-running command (ping).

GET  /api/v1/projects/{project_id}/runs/{run_id}            -> Run (mirror), 404 if not the caller's
POST /api/v1/projects/{project_id}/runs/ping?queue=sbam-llm -> 202 {"run": Run}

Both are FDE routes on an assigned project (`project_access`: other tenant / unassigned -> 404).

The ping command exists to prove the plumbing end to end (API -> Temporal -> worker on any queue ->
mirror). It is off unless ENABLE_PING=true (local/dev only).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sbam_contracts.runs import (
    Run,
    RunAccepted,
    RunInput,
    SubjectType,
    TaskQueue,
    ping_workflow_id,
)
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.conventions import not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.core.settings import Settings
from sbam_api.temporal.deps import session_factory, temporal_client
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.temporal.service import StartSpec, start_run

router = APIRouter(tags=["runs"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]


@router.get("/projects/{project_id}/runs/{run_id}", response_model=Run)
def get_run(project_id: Project, run_id: UUID, user: User, sessions: Sessions) -> Run:
    with sessions() as session:
        row = TemporalRunRepository(session, user.context(project_id)).get(run_id)
        if row is None:  # missing and other-tenant look the same
            raise not_found()
        return to_run(row)


@router.post("/projects/{project_id}/runs/ping", status_code=202, response_model=RunAccepted)
async def start_ping(
    project_id: Project,
    request: Request,
    user: User,
    sessions: Sessions,
    queue: TaskQueue = TaskQueue.INGEST,
) -> RunAccepted:
    settings: Settings = request.app.state.settings
    if not settings.enable_ping:
        raise not_found()
    client = await temporal_client(request)
    run_input = RunInput(
        tenant_id=user.context(project_id).tenant_id,
        project_id=project_id,
        run_id=uuid7(),
        correlation_id=correlation_id_var.get() or str(uuid7()),
    )
    spec = StartSpec(
        workflow="PingWorkflow",
        workflow_id=ping_workflow_id(queue, project_id),
        queue=queue,
        subject_type=SubjectType.PROJECT,
        subject_id=project_id,
        stage="ping",
        input=run_input,
    )
    run = await start_run(
        client, sessions, spec, with_search_attributes=settings.temporal_search_attributes
    )
    return RunAccepted(run=run)
