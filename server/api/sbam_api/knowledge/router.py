"""Knowledge endpoints (TD §8, E5.2).

* `POST P/knowledge/rebuild`: 202 `{"run": Run}`; the new knowledge version is BUILDING. 404
  unassigned or other tenant; 409 KNOWLEDGE_BUILDING while a version is being built (or the
  pipeline is about to build one), PROJECT_ARCHIVED; 503 TEMPORAL_UNAVAILABLE. A repeated
  `Idempotency-Key` answers the same run.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sbam_contracts.runs import RunAccepted
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.settings import Settings
from sbam_api.knowledge.service import start_rebuild
from sbam_api.pipeline.service import MAX_IDEMPOTENCY_KEY
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["knowledge"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=MAX_IDEMPOTENCY_KEY)]


@router.post(
    "/projects/{project_id}/knowledge/rebuild", status_code=202, response_model=RunAccepted
)
async def rebuild(
    project_id: Project,
    request: Request,
    user: User,
    sessions: Sessions,
    idempotency_key: IdempotencyKey = None,
) -> RunAccepted:
    settings: Settings = request.app.state.settings
    run = await start_rebuild(
        sessions,
        lambda: temporal_client(request),
        user,
        project_id,
        idempotency_key=idempotency_key,
        with_search_attributes=settings.temporal_search_attributes,
    )
    return RunAccepted(run=run)
