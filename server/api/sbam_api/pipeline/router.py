"""Pipeline endpoints (TD §8): POST P/pipeline/start (E10.1, minimal).

202 `{"run": Run}`; 409 PIPELINE_RUNNING when this project's pipeline is running; 503 when Temporal
is down; 404 for a project the FDE isn't assigned to (#48). #69 extends this same route with the
project-ACTIVE / pipeline_state / NO_DOCUMENTS checks once the projects table exists (#54).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request
from sbam_contracts.pipeline import PipelineInput, PipelineStartRequest
from sbam_contracts.runs import RunAccepted
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.core.settings import Settings
from sbam_api.pipeline.service import start_pipeline
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["pipeline"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]


@router.post("/projects/{project_id}/pipeline/start", status_code=202, response_model=RunAccepted)
async def start(
    project_id: Project,
    request: Request,
    user: User,
    sessions: Sessions,
    body: Annotated[PipelineStartRequest | None, Body()] = None,
) -> RunAccepted:
    settings: Settings = request.app.state.settings
    client = await temporal_client(request)
    pipeline_input = PipelineInput(
        tenant_id=user.context(project_id).tenant_id,
        project_id=project_id,
        run_id=uuid7(),
        correlation_id=correlation_id_var.get() or str(uuid7()),
        actor_id=str(user.user_id),
        document_version_ids=body.document_version_ids if body else [],
        search_attributes=settings.temporal_search_attributes,
    )
    run = await start_pipeline(
        client, sessions, pipeline_input, with_search_attributes=settings.temporal_search_attributes
    )
    return RunAccepted(run=run)
