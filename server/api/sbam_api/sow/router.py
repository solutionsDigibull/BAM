"""SOW endpoints (TD §8.7, E6.3). FDE of the project only; another tenant or project is 404.

* `GET P/sow`: the SOW, its current and approved versions, and the ETag a save sends.
* `GET P/sow/versions`: every version, newest first.
* `GET P/sow/versions/{v}`: TipTap + structured + citations; `ETag` header.
* `POST P/sow/versions {content_tiptap, base_version}` + `If-Match`: 201 a new version (EDITED,
  IN_REVIEW) with the new `ETag`; 200 the current version when the content is the same. 428 without
  If-Match, 409 SOW_VERSION_CONFLICT for a stale ETag or base, 422 template / schema / citation,
  423 SOW_LOCKED when approved, 413 over 2 MB.
* `GET P/sow/versions/{v}/diff/{other}`: section by section, what changed from v to other.
* `POST P/sow/new-version {reason}`: 201 the scope-change draft (copy of the approved version,
  IN_REVIEW) with the new `ETag`; 409 NO_APPROVED_SOW / DRAFT_EXISTS; see `new_version.py` (E7.2).
* `GET P/sow/versions/{v}/impact`: which Macro / Sub-Skills approving draft v invalidates; 409
  NO_APPROVED_SOW, or NOT_IN_REVIEW when v is not a draft derived from the approved version.
* `POST P/sow/regenerate {hint?}` + `Idempotency-Key`: 202 `{"run": Run}`; see `regenerate.py`.
* `POST P/sow/versions/{v}/approve {sha256}`: the hash-bound approval (`approve.py`, E7.1).
* `GET P/sow/versions/{v}/citations/{okf_ref_id}` (+ `/source`): the citation panel
  (`citations.py`).
* `GET P/sow/versions/{v}/export?format=pdf|docx|md`: 302 to a signed URL (300 s); see `exports.py`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from sbam_contracts.runs import RunAccepted
from sbam_contracts.sow_api import (
    ExportFormat,
    SowApproval,
    SowApproveRequest,
    SowCitationDetail,
    SowDiff,
    SowImpact,
    SowNewVersionRequest,
    SowOverview,
    SowRegenerateRequest,
    SowSaveRequest,
    SowVersionDetail,
    SowVersionList,
    SowVersionSummary,
)
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.conventions import content_disposition, validate_idempotency_key
from sbam_api.core.logging import correlation_id_var
from sbam_api.core.settings import Settings
from sbam_api.pipeline.service import MAX_IDEMPOTENCY_KEY
from sbam_api.sow import citations, impact, versions
from sbam_api.sow.approve import approve_version
from sbam_api.sow.exports import export_key
from sbam_api.sow.new_version import start_new_version
from sbam_api.sow.regenerate import start_regenerate
from sbam_api.storage.router import backend
from sbam_api.storage.signing import signed_url
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["sow"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
Version = Annotated[int, Path(ge=1, le=100_000)]
IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=MAX_IDEMPOTENCY_KEY)]


@router.get("/projects/{project_id}/sow")
def sow(project_id: Project, user: User, sessions: Sessions) -> SowOverview:
    with sessions() as session:
        return versions.overview(session, user, project_id)


@router.get("/projects/{project_id}/sow/versions")
def sow_versions(project_id: Project, user: User, sessions: Sessions) -> SowVersionList:
    with sessions() as session:
        return versions.list_versions(session, user, project_id)


@router.get("/projects/{project_id}/sow/versions/{version}")
def sow_version(
    project_id: Project, version: Version, response: Response, user: User, sessions: Sessions
) -> SowVersionDetail:
    with sessions() as session:
        out, tag = versions.detail(session, user, project_id, version)
    response.headers["ETag"] = tag
    return out


@router.post("/projects/{project_id}/sow/versions", status_code=201)
def save_sow_version(
    project_id: Project,
    body: SowSaveRequest,
    request: Request,
    response: Response,
    user: User,
    sessions: Sessions,
    if_match: Annotated[str | None, Header()] = None,
) -> SowVersionSummary:
    with sessions() as session:
        saved = versions.save(
            session,
            backend(request),
            user,
            project_id,
            body,
            if_match,
            correlation_id_var.get() or "sow-save",
        )
    response.headers["ETag"] = saved.etag
    if not saved.created:
        response.status_code = 200
    return saved.version


@router.post("/projects/{project_id}/sow/new-version", status_code=201)
def sow_new_version(
    project_id: Project,
    body: SowNewVersionRequest,
    response: Response,
    user: User,
    sessions: Sessions,
) -> SowVersionSummary:
    with sessions() as session:
        saved = start_new_version(
            session, user, project_id, body, correlation_id_var.get() or "sow-new-version"
        )
    response.headers["ETag"] = saved.etag
    return saved.version


@router.get("/projects/{project_id}/sow/versions/{version}/impact")
def sow_impact(project_id: Project, version: Version, user: User, sessions: Sessions) -> SowImpact:
    with sessions() as session:
        return impact.preview(session, user, project_id, version)


@router.get("/projects/{project_id}/sow/versions/{version}/diff/{other}")
def sow_diff(
    project_id: Project,
    version: Version,
    other: Annotated[int, Path(ge=1, le=100_000)],
    user: User,
    sessions: Sessions,
) -> SowDiff:
    with sessions() as session:
        return versions.diff(session, user, project_id, version, other)


@router.post("/projects/{project_id}/sow/regenerate", status_code=202, response_model=RunAccepted)
async def regenerate_sow(
    project_id: Project,
    request: Request,
    user: User,
    sessions: Sessions,
    body: SowRegenerateRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> RunAccepted:
    if idempotency_key is not None:
        validate_idempotency_key(idempotency_key)
    settings: Settings = request.app.state.settings
    run = await start_regenerate(
        sessions,
        lambda: temporal_client(request),
        user,
        project_id,
        body.hint if body else None,
        idempotency_key=idempotency_key,
        with_search_attributes=settings.temporal_search_attributes,
    )
    return RunAccepted(run=run)


EXPORT_URL_TTL = 300


@router.get(
    "/projects/{project_id}/sow/versions/{version}/export",
    status_code=302,
    response_class=RedirectResponse,
)
async def export_sow_version(
    project_id: Project,
    version: Version,
    format: Annotated[ExportFormat, Query()],  # the API's name for it
    request: Request,
    user: User,
    sessions: Sessions,
) -> RedirectResponse:
    key = await export_key(
        sessions, lambda: temporal_client(request), user, project_id, version, format
    )
    settings: Settings = request.app.state.settings
    secret = settings.storage_signing_key
    url = signed_url(
        secret.get_secret_value() if secret else None,
        key,
        user.context(project_id),
        EXPORT_URL_TTL,
    )
    return RedirectResponse(url, status_code=302)


OkfRefId = Annotated[str, Path(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9#:._-]+$")]


@router.get("/projects/{project_id}/sow/versions/{version}/citations/{okf_ref_id}")
def sow_citation(
    project_id: Project,
    version: Version,
    okf_ref_id: OkfRefId,
    request: Request,
    user: User,
    sessions: Sessions,
) -> SowCitationDetail:
    with sessions() as session:
        return citations.detail(session, backend(request), user, project_id, version, okf_ref_id)


@router.get(
    "/projects/{project_id}/sow/versions/{version}/citations/{okf_ref_id}/source",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}},
)
def sow_citation_source(
    project_id: Project,
    version: Version,
    okf_ref_id: OkfRefId,
    request: Request,
    user: User,
    sessions: Sessions,
) -> Response:
    with sessions() as session:
        found = citations.source(session, backend(request), user, project_id, version, okf_ref_id)
    return Response(
        found.text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(found.filename),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/projects/{project_id}/sow/versions/{version}/approve")
async def approve_sow_version(
    project_id: Project,
    version: Version,
    body: SowApproveRequest,
    request: Request,
    user: User,
    sessions: Sessions,
) -> SowApproval:
    settings: Settings = request.app.state.settings
    return await approve_version(
        sessions,
        lambda: temporal_client(request),
        user,
        project_id,
        version,
        body,
        with_search_attributes=settings.temporal_search_attributes,
    )
