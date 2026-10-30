"""Tenant SRIM runtime defaults (TD §11.2, O-19, E9.1b).

GET /api/v1/admin/srim-defaults -> the tenant's values (ISA14's until it sets its own), with an ETag
PUT /api/v1/admin/srim-defaults -> replace all six blocks; `If-Match` required; audited
DELETE ...                      -> back to the ISA14 values; `If-Match` required; audited
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sbam_contracts.manifest import SrimDefaultsView, SrimRuntimeDefaults
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser, admin
from sbam_api.core.conventions import etag
from sbam_api.db.session import app_sessions
from sbam_api.srim_defaults import service

router = APIRouter(tags=["srim-defaults"])

Admin = Annotated[CurrentUser, Depends(admin)]


def db(request: Request) -> Iterator[Session]:
    with app_sessions(request.app.state)() as session:
        yield session


Db = Annotated[Session, Depends(db)]


@router.get("/admin/srim-defaults")
def get_srim_defaults(user: Admin, session: Db, response: Response) -> SrimDefaultsView:
    view, version = service.get(session, user)
    response.headers["ETag"] = etag(version)
    return view


@router.put("/admin/srim-defaults")
def put_srim_defaults(
    body: SrimRuntimeDefaults,
    user: Admin,
    session: Db,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> SrimDefaultsView:
    view, version = service.put(session, user, body, if_match)
    response.headers["ETag"] = etag(version)
    return view


@router.delete("/admin/srim-defaults")
def reset_srim_defaults(
    user: Admin,
    session: Db,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> SrimDefaultsView:
    view, version = service.reset(session, user, if_match)
    response.headers["ETag"] = etag(version)
    return view
