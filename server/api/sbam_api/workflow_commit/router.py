"""Commit routes (#766). W is `/projects/{project_id}/workflows/{workflow_id}`.

* `GET W/commit`: the checks, the secrets gate and what Commit did; `ETag` = the combined
  manifest hash.
* `POST W/commit` with `If-Match` and `Idempotency-Key`: 202 with the run; 409 for a stale hash or a
  failed check; 428 without `If-Match`.

Only the assigned FDE (`active_project`): another tenant, an unassigned FDE and a missing Workflow
are 404, a non-FDE role gets the portal answer the other FDE routes give.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from sbam_contracts.workflow_commit import CommitAccepted, CommitStatus
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import validate_idempotency_key
from sbam_api.projects.guard import active_project
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.router import backend
from sbam_api.temporal.deps import session_factory, temporal_client
from sbam_api.workflow_commit import service

router = APIRouter(tags=["workflow-commit"])

Project = Annotated[UUID, Depends(active_project)]  # assigned FDE; archived: writes 409
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
Storage = Annotated[StorageBackend, Depends(backend)]
W = "/projects/{project_id}/workflows/{workflow_id}"


@router.get(W + "/commit")
def commit_status(
    project_id: Project,
    workflow_id: UUID,
    user: User,
    sessions: Sessions,
    storage: Storage,
    response: Response,
) -> CommitStatus:
    with sessions() as session:
        found = service.status(session, user.context(project_id), workflow_id, storage)
    response.headers["ETag"] = service.etag_of(found.manifest_hash)
    return found


@router.post(W + "/commit", status_code=202, response_model=CommitAccepted)
async def commit(
    project_id: Project,
    workflow_id: UUID,
    request: Request,
    user: User,
    sessions: Sessions,
    storage: Storage,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header()] = None,
) -> CommitAccepted:
    key = validate_idempotency_key(idempotency_key)
    return await service.request_commit(
        sessions,
        storage,
        lambda: temporal_client(request),
        user,
        project_id,
        workflow_id,
        if_match,
        key,
    )
