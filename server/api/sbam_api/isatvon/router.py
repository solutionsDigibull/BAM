"""GET /api/v1/schemas/isatvon/{version}: JSON Schema plus grid metadata (TD §8.8, E8.6).

Any signed-in user may read it; it describes the model and carries no tenant data. Supports
conditional requests: a matching `If-None-Match` answers 304 with no body.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from sbam_contracts.api import ProblemDetail

from sbam_api.core.auth import authenticated
from sbam_api.core.conventions import not_found
from sbam_api.isatvon.grid import IsatvonSchemaResponse, render

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
