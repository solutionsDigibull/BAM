"""ISATVON endpoints (TD §8.8).

* `GET /api/v1/schemas/isatvon/{version}`: JSON Schema plus grid metadata (E8.6). Any signed-in
  user may read it; it describes the model and carries no tenant data. Supports conditional
  requests: a matching `If-None-Match` answers 304 with no body.
* `POST W/isatvon/validate` (E8.3): every Sub-Skill of the Workflow revalidated with the extraction
  and build validator (`ValidationReport`). Read-only. FDE portal; another tenant's or an
  unassigned project, or a Workflow of another project, is 404.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from sbam_contracts.api import ProblemDetail
from sbam_contracts.isatvon_workbench import ValidationReport
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, authenticated, fde, project_access
from sbam_api.core.conventions import not_found
from sbam_api.isatvon.grid import IsatvonSchemaResponse, render
from sbam_api.isatvon.service import revalidate
from sbam_api.temporal.deps import session_factory

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
