"""Sandbox run endpoints (E13.2). FDE portal.

* `GET W/sandbox-runs/{run_id}/logs`: 302 to a short-lived signed download of the run's stdout and
  stderr. The logs are what the code under test printed, so they are served as an attachment, never
  rendered. 404 (and an `access_denied` audit row) for another tenant's run, an unknown id, another
  Workflow's run and a run that stored no logs.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import ApiError
from sbam_api.core.settings import Settings
from sbam_api.projects.guard import active_project
from sbam_api.sandbox import service
from sbam_api.storage.signing import signed_url
from sbam_api.temporal.deps import session_factory

router = APIRouter(tags=["sandbox"])

W = "/projects/{project_id}/workflows/{workflow_id}"
LOGS_URL_TTL = 300
Project = Annotated[uuid.UUID, Depends(active_project)]
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]


@router.get(W + "/sandbox-runs/{run_id}/logs", status_code=302, response_class=RedirectResponse)
def run_logs(
    project_id: Project,
    workflow_id: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    user: User,
    sessions: Sessions,
) -> RedirectResponse:
    ctx = user.context(project_id)
    try:
        with sessions() as session:
            key = service.logs_key(session, ctx, workflow_id, run_id)
    except ApiError as error:
        if error.status == 404:  # a caller probing for run ids is worth an audit entry
            request.app.state.auth.audit.denied(
                actor=user, route=request.url.path, target=str(run_id), reason="sandbox run"
            )
        raise
    settings: Settings = request.app.state.settings
    secret = settings.storage_signing_key
    url = signed_url(secret.get_secret_value() if secret else None, key, ctx, LOGS_URL_TTL)
    return RedirectResponse(url, status_code=302)
