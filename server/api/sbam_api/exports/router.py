"""Export routes (#133, E14.5). W is `/projects/{project_id}/workflows/{workflow_id}`.

* `POST W/versions/{version}/export` with `Idempotency-Key` (a UUID): 202 with the run; 409
  `NOT_FROZEN` for a version that is not frozen.
* `GET W/versions/{version}/export`: 302 to a short-lived signed URL; 404 when not built.

Only the assigned FDE (`active_project`): another tenant, an unassigned FDE and a missing Workflow
are 404.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import RedirectResponse
from sbam_contracts.exports import ExportAccepted
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import validate_idempotency_key
from sbam_api.core.settings import Settings
from sbam_api.exports import service
from sbam_api.projects.guard import active_project
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["exports"])

Project = Annotated[UUID, Depends(active_project)]
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
E = "/projects/{project_id}/workflows/{workflow_id}/versions/{version}/export"


@router.post(E, status_code=202, response_model=ExportAccepted)
async def export_version(
    project_id: Project,
    workflow_id: UUID,
    version: str,
    request: Request,
    user: User,
    sessions: Sessions,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExportAccepted:
    key = validate_idempotency_key(idempotency_key)
    return await service.request_export(
        sessions, lambda: temporal_client(request), user, project_id, workflow_id, version, key
    )


@router.get(E, status_code=302, response_class=RedirectResponse)
def export_download(
    project_id: Project,
    workflow_id: UUID,
    version: str,
    request: Request,
    user: User,
    sessions: Sessions,
) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    secret = settings.storage_signing_key
    with sessions() as session:
        url = service.download_url(
            session,
            user.context(project_id),
            workflow_id,
            version,
            secret.get_secret_value() if secret else None,
        )
    return RedirectResponse(url, status_code=302)
