"""Token caps and the meter (#776, ADR 0028 decision 6).

- `GET`/`PUT /admin/projects/{id}/token-caps`: an Admin reads and changes the caps of one project
  (`If-Match`; the platform defaults apply while nothing is stored).
- `GET /projects/{id}/token-budget`: the FDE's meter, used against the cap per scope.
- `POST /projects/{id}/token-budget/raise` (FDE) and `POST /admin/projects/{id}/token-budget/raise`
  (Admin): raise one scope's cap once per build, then Retry.

Another tenant's project, or one the FDE is not assigned to, is a 404 on every route.
"""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sbam_contracts.budget import BudgetCaps, BudgetState, ProjectTokenCaps, RaiseCap, TokenMeter
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser, admin, fde
from sbam_api.core.conventions import etag
from sbam_api.db.session import app_sessions
from sbam_api.projects.guard import active_project
from sbam_api.token_budget import service

router = APIRouter(tags=["token-budget"])

Admin = Annotated[CurrentUser, Depends(admin)]
Fde = Annotated[CurrentUser, Depends(fde)]


def db(request: Request) -> Iterator[Session]:
    with app_sessions(request.app.state)() as session:
        yield session


Db = Annotated[Session, Depends(db)]
ProjectId = Annotated[uuid.UUID, Depends(active_project)]


def _caps_out(caps: ProjectTokenCaps, response: Response) -> ProjectTokenCaps:
    response.headers["ETag"] = etag(caps.row_version)
    return caps


@router.get("/admin/projects/{project_id}/token-caps")
def get_caps(
    project_id: uuid.UUID, user: Admin, session: Db, response: Response
) -> ProjectTokenCaps:
    return _caps_out(service.get_caps(session, user, project_id), response)


@router.put("/admin/projects/{project_id}/token-caps")
def put_caps(
    project_id: uuid.UUID,
    body: BudgetCaps,
    user: Admin,
    session: Db,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> ProjectTokenCaps:
    return _caps_out(service.put_caps(session, user, project_id, body, if_match), response)


@router.get("/projects/{project_id}/token-budget")
def token_meter(project_id: ProjectId, user: Fde, session: Db) -> TokenMeter:
    return service.meter(session, user, project_id)


@router.post("/projects/{project_id}/token-budget/raise")
def raise_cap_fde(project_id: ProjectId, body: RaiseCap, user: Fde, session: Db) -> BudgetState:
    return service.raise_cap(session, user, project_id, body)


@router.post("/admin/projects/{project_id}/token-budget/raise")
def raise_cap_admin(project_id: uuid.UUID, body: RaiseCap, user: Admin, session: Db) -> BudgetState:
    return service.raise_cap(session, user, project_id, body)
