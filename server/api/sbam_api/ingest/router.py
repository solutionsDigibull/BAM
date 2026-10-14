"""Upload endpoints (TD §8.5): `POST /projects/{id}/uploads` and the tusd hook (E4.2).

The hook is called by tusd, not by a user: it authenticates with `X-Tusd-Hook-Secret` (compared in
constant time; 401 otherwise and nothing is read or written). It answers 200 with tusd's hook
response, where a refused upload is `RejectUpload` with the problem+json body.
"""

import asyncio
import hmac
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import ValidationError
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import UploadRequest, UploadToken
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, internal, project_access
from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.core.settings import Settings
from sbam_api.db.repository import TenantContext
from sbam_api.ingest import service, tusd
from sbam_api.ingest.ingestion import start_waiting
from sbam_api.storage.router import backend
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["ingest"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]

STAGING = "uploads"  # tusd's -upload-dir, under STORAGE_ROOT (compose/compose.yaml)


def _secret(settings: Settings) -> str:
    if settings.tusd_hook_secret is None:  # fail closed: no secret, no uploads
        raise ApiError(
            503, ErrorCode.SERVICE_UNAVAILABLE, "Uploads unavailable", "Uploads are not configured."
        )
    return settings.tusd_hook_secret.get_secret_value()


@router.post("/projects/{project_id}/uploads", status_code=201, response_model=UploadToken)
def create_upload_token(
    project_id: Project,
    body: UploadRequest,
    request: Request,
    user: User,
    sessions: Sessions,
) -> UploadToken:
    settings: Settings = request.app.state.settings
    secret = _secret(settings)
    with sessions() as session:
        return service.request_upload(
            session, user, project_id, body, secret, settings.tusd_public_url
        )


def hook_secret(
    request: Request, x_tusd_hook_secret: Annotated[str | None, Header()] = None
) -> str:
    """The configured secret, once the caller proved it knows it. Runs before the body is read."""
    expected = _secret(request.app.state.settings)
    supplied = (x_tusd_hook_secret or "").encode()
    if not hmac.compare_digest(supplied, expected.encode()):
        raise ApiError(401, ErrorCode.UNAUTHENTICATED, "Invalid hook secret")
    return expected


@router.post("/internal/tusd/hooks", dependencies=[Depends(internal)])
async def tusd_hook(
    request: Request, secret: Annotated[str, Depends(hook_secret)], sessions: Sessions
) -> dict[str, object]:
    try:
        hook = tusd.HookRequest.model_validate_json(await request.body())
    except ValidationError:
        raise ApiError(
            422, ErrorCode.VALIDATION_ERROR, "Invalid hook request", "Not a tusd hook request."
        ) from None
    correlation = correlation_id_var.get() or str(uuid7())
    upload = hook.event.upload
    if hook.type == "pre-create":
        return await asyncio.to_thread(_pre_create, sessions, secret, upload, correlation)
    if hook.type == "post-finish":
        await _post_finish(request, sessions, secret, upload, correlation)
    return tusd.accept()


def _pre_create(
    sessions: sessionmaker[Session], secret: str, upload: tusd.HookUpload, correlation: str
) -> dict[str, object]:
    with sessions() as session:
        decision = service.pre_create(
            session, secret, upload.meta_data, upload.size, upload.size_is_deferred
        )
    if decision.code is not None:
        return tusd.reject(decision.status, decision.code, decision.detail, correlation)
    return tusd.accept(decision.upload_id)


def _register(
    sessions: sessionmaker[Session], request: Request, secret: str, upload: tusd.HookUpload
) -> service.Registered | None:
    settings: Settings = request.app.state.settings
    with sessions() as session:
        return service.post_finish(
            session,
            backend(request),
            secret,
            Path(settings.storage_root) / STAGING,
            upload.id,
            upload.meta_data,
            upload.offset,
            upload.size,
        )


async def _post_finish(
    request: Request,
    sessions: sessionmaker[Session],
    secret: str,
    upload: tusd.HookUpload,
    correlation: str,
) -> None:
    registered = await asyncio.to_thread(_register, sessions, request, secret, upload)
    if registered is None:
        return
    # The version is registered. Handing it to Temporal is best effort: if Temporal is down the
    # version waits and the upload still succeeds (E4.2-T10).
    settings: Settings = request.app.state.settings
    ctx = TenantContext(registered.tenant_id, registered.project_id)
    try:
        client = await temporal_client(request)
    except ApiError:
        return
    # This also starts the version that was just registered (it is waiting too).
    await start_waiting(
        client,
        sessions,
        ctx,
        correlation,
        with_search_attributes=settings.temporal_search_attributes,
    )
