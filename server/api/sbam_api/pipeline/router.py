"""Project workspace pipeline endpoints (TD §8.4, E4.1; start from E10.1):

* `POST P/pipeline/start`: 202 `{"run": Run}`. 404 unassigned or other tenant; 409 PROJECT_ARCHIVED,
  PIPELINE_RUNNING, NO_DOCUMENTS or INVALID_TRANSITION; 503 TEMPORAL_UNAVAILABLE (state unchanged).
  The documents come from the database, not the request. A repeated `Idempotency-Key` (<= 128
  characters) answers the same run.
* `GET P/pipeline`: the dashboard's state, step timeline and document counts.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sbam_contracts.projects import PipelineTimeline
from sbam_contracts.runs import RunAccepted
from sbam_contracts.state_machines import ProjectPipelineState
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.audit.repository import entity_history
from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.conventions import not_found
from sbam_api.core.settings import Settings
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.pipeline.service import MAX_IDEMPOTENCY_KEY, start_project_pipeline
from sbam_api.pipeline.timeline import build_counts, build_steps
from sbam_api.projects.repository import ProjectRepository
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["pipeline"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=MAX_IDEMPOTENCY_KEY)]


@router.post("/projects/{project_id}/pipeline/start", status_code=202, response_model=RunAccepted)
async def start(
    project_id: Project,
    request: Request,
    user: User,
    sessions: Sessions,
    idempotency_key: IdempotencyKey = None,
) -> RunAccepted:
    settings: Settings = request.app.state.settings
    run = await start_project_pipeline(
        sessions,
        lambda: temporal_client(request),
        user,
        project_id,
        idempotency_key=idempotency_key,
        with_search_attributes=settings.temporal_search_attributes,
    )
    return RunAccepted(run=run)


@router.get("/projects/{project_id}/pipeline")
def pipeline(project_id: Project, user: User, sessions: Sessions) -> PipelineTimeline:
    ctx = user.context(project_id)
    with sessions() as session:
        project = ProjectRepository(session, ctx).get(project_id)
        if project is None:
            raise not_found()
        state = ProjectPipelineState(project.state)
        history = entity_history(session, ctx.tenant_id, "project_pipeline", str(project_id))
        counts = DocumentVersionRepository(session, ctx).count_by_state()
    return PipelineTimeline(
        state=state, steps=build_steps(state, history), counts=build_counts(counts)
    )
