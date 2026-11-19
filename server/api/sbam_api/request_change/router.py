"""Request change routes (#767). W is `/projects/{project_id}/workflows/{workflow_id}`."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sbam_contracts.request_change import ChangeNoteList, RequestChangeAccepted, RequestChangeCreate
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import validate_idempotency_key
from sbam_api.projects.guard import active_project
from sbam_api.request_change import service
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["request-change"])

Project = Annotated[UUID, Depends(active_project)]  # assigned FDE; archived: writes 409
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
W = "/projects/{project_id}/workflows/{workflow_id}"


@router.get(W + "/request-change")
def change_notes(
    project_id: Project, workflow_id: UUID, user: User, sessions: Sessions
) -> ChangeNoteList:
    """The notes of the Workflow's current version, with what became of each."""
    with sessions() as session:
        return service.list_notes(session, user, project_id, workflow_id)


@router.post(W + "/request-change", status_code=202, response_model=RequestChangeAccepted)
async def request_change(
    project_id: Project,
    workflow_id: UUID,
    body: RequestChangeCreate,
    request: Request,
    user: User,
    sessions: Sessions,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RequestChangeAccepted:
    key = validate_idempotency_key(idempotency_key)
    return await service.request_change(
        sessions, lambda: temporal_client(request), user, project_id, workflow_id, body, key
    )
