"""ISATVON endpoints (TD §8.8).

* `GET /api/v1/schemas/isatvon/{version}`: JSON Schema plus grid metadata (E8.6). Any signed-in
  user may read it; it describes the model and carries no tenant data. Supports conditional
  requests: a matching `If-None-Match` answers 304 with no body.
* `POST W/isatvon/validate` (E8.3): every Sub-Skill of the Workflow revalidated with the extraction
  and build validator (`ValidationReport`). Read-only. FDE portal; another tenant's or an
  unassigned project, or a Workflow of another project, is 404.
* `PATCH W/sub-skills/{sub_id}` (E8.4, `SubSkillPatch` + `If-Match` = the row's `content_sha256`):
  merges the edit, revalidates, 200 the whole row (VALID, or INVALID with findings); a type error is
  422 and stores nothing. `PATCH W/macro-skills/{macro_id}` (`MacroSkillPatch`, `If-Match` =
  row_version), `DELETE W/sub-skills/{sub_id}` (204) and `GET W/sub-skills/{sub_id}/edits` (the
  per-field history). A Sub-Skill cannot be created here: POST is 405. See `isatvon/edits.py`.
* `POST W/macro-skills/{macro_id}/regenerate` and `POST W/sub-skills/{sub_id}/regenerate`
  (`RegenerateRequest {hint?}` + `Idempotency-Key`): 202 `{"run": Run}`; see `regenerate.py`.
* `GET W/isatvon/export.xlsx?include_invalid=true` (E8.5): 302 to a signed URL (300 s) of the
  breakdown as a workbook, a sheet per Macro Skill; 409 CONFLICT before any Macro Skill exists.
  See `isatvon/export.py`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import RedirectResponse
from sbam_contracts.api import ProblemDetail
from sbam_contracts.isatvon_macros import MacroSkillOut
from sbam_contracts.isatvon_workbench import (
    MacroSkillPatch,
    RegenerateRequest,
    SubSkillEditHistory,
    SubSkillPatch,
    ValidationReport,
    WorkbenchSubSkill,
)
from sbam_contracts.runs import RunAccepted
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, authenticated, fde, project_access
from sbam_api.core.conventions import etag, not_found, validate_idempotency_key
from sbam_api.core.settings import Settings
from sbam_api.isatvon import edits
from sbam_api.isatvon.export import export_workbook
from sbam_api.isatvon.grid import IsatvonSchemaResponse, render
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.isatvon.regenerate import start_regenerate
from sbam_api.isatvon.service import revalidate
from sbam_api.pipeline.service import MAX_IDEMPOTENCY_KEY
from sbam_api.storage.router import backend
from sbam_api.storage.signing import signed_url
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["isatvon"])
CACHE_CONTROL = "private, max-age=300, must-revalidate"


def _matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 9110 weak comparison of every entity-tag in the header; `*` matches any."""
    if not if_none_match:
        return False
    tags = [t.strip().removeprefix("W/") for t in if_none_match.split(",")]
    return "*" in tags or etag in tags


@router.get(
    "/schemas/isatvon/{version}",
    response_model=IsatvonSchemaResponse,
    dependencies=[Depends(authenticated)],
    responses={
        304: {"description": "If-None-Match matched the ETag; no body"},
        404: {"model": ProblemDetail, "description": "Version not served"},
    },
)
def isatvon_schema(version: str, if_none_match: Annotated[str | None, Header()] = None) -> Response:
    rendered = render(version)
    if rendered is None:
        raise not_found()
    headers = {"ETag": rendered.etag, "Cache-Control": CACHE_CONTROL}
    if _matches(if_none_match, rendered.etag):
        return Response(status_code=304, headers=headers)
    return Response(content=rendered.body, media_type="application/json", headers=headers)


@router.post("/projects/{project_id}/workflows/{workflow_id}/isatvon/validate")
def validate(
    project_id: Annotated[UUID, Depends(project_access)],
    workflow_id: UUID,
    user: Annotated[CurrentUser, Depends(fde)],
    sessions: Annotated[sessionmaker[Session], Depends(session_factory)],
) -> ValidationReport:
    with sessions() as session:
        return revalidate(session, user.context(project_id), workflow_id)


W = "/projects/{project_id}/workflows/{workflow_id}"
Project = Annotated[UUID, Depends(project_access)]
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
IfMatch = Annotated[str | None, Header()]
IdempotencyKey = Annotated[str | None, Header(min_length=1, max_length=MAX_IDEMPOTENCY_KEY)]


def _row(sub: SubSkill) -> WorkbenchSubSkill:
    return WorkbenchSubSkill.model_validate(
        {
            "id": sub.id,
            "macro_id": sub.macro_skill_id,
            "state": sub.state,
            "content_sha256": sub.content_sha256,
            "validation_errors": sub.validation_errors,
            "sow_refs": sub.sow_refs,
            "sub_skill": sub.isatvon,
        }
    )


@router.patch(W + "/sub-skills/{sub_id}")
def edit_sub_skill(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    body: SubSkillPatch,
    user: User,
    sessions: Sessions,
    response: Response,
    if_match: IfMatch = None,
) -> WorkbenchSubSkill:
    with sessions() as session:
        sub = edits.edit_sub_skill(session, user, project_id, workflow_id, sub_id, body, if_match)
        session.commit()
        response.headers["ETag"] = f'"{sub.content_sha256}"'
        return _row(sub)


@router.delete(W + "/sub-skills/{sub_id}", status_code=204)
def delete_sub_skill(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    user: User,
    sessions: Sessions,
    if_match: IfMatch = None,
) -> Response:
    with sessions() as session:
        edits.delete_sub_skill(session, user, project_id, workflow_id, sub_id, if_match)
        session.commit()
    return Response(status_code=204)


@router.get(W + "/sub-skills/{sub_id}/edits")
def sub_skill_edits(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    user: User,
    sessions: Sessions,
) -> SubSkillEditHistory:
    with sessions() as session:
        return edits.edit_history(session, user.context(project_id), workflow_id, sub_id)


@router.patch(W + "/macro-skills/{macro_id}")
def edit_macro_skill(
    project_id: Project,
    workflow_id: UUID,
    macro_id: UUID,
    body: MacroSkillPatch,
    user: User,
    sessions: Sessions,
    response: Response,
    if_match: IfMatch = None,
) -> MacroSkillOut:
    with sessions() as session:
        macro: MacroSkill = edits.edit_macro_skill(
            session, user, project_id, workflow_id, macro_id, body, if_match
        )
        session.commit()
        response.headers["ETag"] = etag(macro.row_version)
        return MacroSkillOut.model_validate(macro, from_attributes=True)


async def _regenerate(
    project_id: UUID,
    workflow_id: UUID,
    entity_id: UUID,
    body: RegenerateRequest | None,
    request: Request,
    user: CurrentUser,
    sessions: sessionmaker[Session],
    idempotency_key: str | None,
    is_sub: bool,
) -> RunAccepted:
    if idempotency_key is not None:
        validate_idempotency_key(idempotency_key)
    settings: Settings = request.app.state.settings
    run = await start_regenerate(
        sessions,
        lambda: temporal_client(request),
        user,
        project_id,
        workflow_id,
        entity_id,
        body.hint if body else None,
        is_sub=is_sub,
        idempotency_key=idempotency_key,
        with_search_attributes=settings.temporal_search_attributes,
    )
    return RunAccepted(run=run)


@router.post(W + "/macro-skills/{macro_id}/regenerate", status_code=202, response_model=RunAccepted)
async def regenerate_macro_skill(
    project_id: Project,
    workflow_id: UUID,
    macro_id: UUID,
    request: Request,
    user: User,
    sessions: Sessions,
    body: RegenerateRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> RunAccepted:
    return await _regenerate(
        project_id, workflow_id, macro_id, body, request, user, sessions, idempotency_key, False
    )


@router.post(W + "/sub-skills/{sub_id}/regenerate", status_code=202, response_model=RunAccepted)
async def regenerate_sub_skill(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    request: Request,
    user: User,
    sessions: Sessions,
    body: RegenerateRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> RunAccepted:
    return await _regenerate(
        project_id, workflow_id, sub_id, body, request, user, sessions, idempotency_key, True
    )


EXPORT_URL_TTL = 300  # seconds a signed export link works (as SOW exports)


@router.get(W + "/isatvon/export.xlsx", status_code=302, response_class=RedirectResponse)
def export_isatvon(
    project_id: Project,
    workflow_id: UUID,
    request: Request,
    user: User,
    sessions: Sessions,
    include_invalid: Annotated[bool, Query()] = True,
) -> RedirectResponse:
    with sessions() as session:
        made = export_workbook(
            session,
            backend(request),
            user,
            project_id,
            workflow_id,
            include_invalid=include_invalid,
        )
    settings: Settings = request.app.state.settings
    secret = settings.storage_signing_key
    url = signed_url(
        secret.get_secret_value() if secret else None,
        made.storage_key,
        user.context(project_id),
        EXPORT_URL_TTL,
    )
    return RedirectResponse(url, status_code=302, headers={"X-Content-SHA256": made.sha256})
