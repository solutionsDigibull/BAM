"""Projects API (E3.1): admin CRUD + archive under `/admin/projects`, and the FDE's `/projects`.
Members (E2.4): `GET/PUT /admin/projects/{id}/members`.
"""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sbam_contracts.api import Page
from sbam_contracts.projects import Project as ProjectOut
from sbam_contracts.projects import ProjectCreate, ProjectMembers, ProjectMembersPut, ProjectPatch
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser, admin, fde
from sbam_api.core.conventions import etag
from sbam_api.db.session import app_sessions
from sbam_api.identity.models import User
from sbam_api.identity.router import tenant_user
from sbam_api.projects import service
from sbam_api.projects.models import Project

router = APIRouter(tags=["projects"])

Admin = Annotated[CurrentUser, Depends(admin)]


def db(request: Request) -> Iterator[Session]:
    with app_sessions(request.app.state)() as session:
        yield session


Db = Annotated[Session, Depends(db)]


def _out(project: Project, response: Response | None = None) -> ProjectOut:
    if response is not None:
        response.headers["ETag"] = etag(project.row_version)
    return ProjectOut.model_validate(
        {
            **{c: getattr(project, c) for c in ProjectOut.model_fields if c != "pipeline_state"},
            "pipeline_state": project.state,
        }
    )


@router.post("/admin/projects", status_code=201)
def create_project(body: ProjectCreate, user: Admin, session: Db, response: Response) -> ProjectOut:
    return _out(service.create(session, user, body), response)


@router.get("/admin/projects")
def list_projects(user: Admin, session: Db) -> Page[ProjectOut]:
    return Page[ProjectOut](items=[_out(p) for p in service.list_all(session, user)])


@router.get("/admin/projects/{project_id}")
def get_project(project_id: uuid.UUID, user: Admin, session: Db, response: Response) -> ProjectOut:
    return _out(service.get(session, user, project_id), response)


@router.patch("/admin/projects/{project_id}")
def patch_project(
    project_id: uuid.UUID,
    body: ProjectPatch,
    user: Admin,
    session: Db,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> ProjectOut:
    return _out(service.update(session, user, project_id, body, if_match), response)


@router.post("/admin/projects/{project_id}/archive")
def archive_project(
    project_id: uuid.UUID, user: Admin, session: Db, response: Response
) -> ProjectOut:
    return _out(service.archive(session, user, project_id), response)


def _members(project_id: uuid.UUID, users: list[User]) -> ProjectMembers:
    return ProjectMembers(project_id=project_id, members=[tenant_user(u) for u in users])


@router.get("/admin/projects/{project_id}/members")
def get_members(project_id: uuid.UUID, user: Admin, session: Db) -> ProjectMembers:
    return _members(project_id, service.members(session, user, project_id))


@router.put("/admin/projects/{project_id}/members")
def put_members(
    project_id: uuid.UUID, body: ProjectMembersPut, user: Admin, session: Db
) -> ProjectMembers:
    return _members(project_id, service.set_members(session, user, project_id, body))


@router.get("/projects")
def my_projects(user: Annotated[CurrentUser, Depends(fde)], session: Db) -> Page[ProjectOut]:
    return Page[ProjectOut](items=[_out(p) for p in service.list_assigned(session, user)])
