"""Identity API: super-admin tenants and the caller's own profile (E2.3), tenant users (E2.4).

`/admin/tenants` is platform-level (super-admin, no tenant); an sbam-admin gets 403 WRONG_PORTAL.
`/admin/users` is the caller's tenant only (another tenant's user: 404).
`/me` answers for any signed-in user.
"""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sbam_contracts.api import Page
from sbam_contracts.identity import (
    Me,
    TenantCreate,
    TenantPatch,
    TenantUser,
    UserInvite,
    UserPatch,
    UserProfile,
)
from sbam_contracts.identity import Tenant as TenantOut
from sqlalchemy.orm import Session

from sbam_api.core.auth import Auth, CurrentUser, admin, authenticated, super_admin
from sbam_api.core.conventions import etag
from sbam_api.db.session import app_sessions
from sbam_api.identity import repository, service
from sbam_api.identity.models import Tenant, User

router = APIRouter(tags=["identity"])

SuperAdmin = Annotated[CurrentUser, Depends(super_admin)]
Admin = Annotated[CurrentUser, Depends(admin)]


def db(request: Request) -> Iterator[Session]:
    with app_sessions(request.app.state)() as session:
        yield session


Db = Annotated[Session, Depends(db)]


def _auth(request: Request) -> Auth:
    auth: Auth = request.app.state.auth
    return auth


def _out(tenant: Tenant) -> TenantOut:
    return TenantOut.model_validate(tenant, from_attributes=True)


@router.post("/admin/tenants", status_code=201)
def create_tenant(
    body: TenantCreate, user: SuperAdmin, session: Db, request: Request, response: Response
) -> TenantOut:
    auth = _auth(request)
    tenant = service.create_tenant(session, auth.orgs, auth.audit, user.keycloak_sub, body.name)
    response.headers["ETag"] = etag(tenant.row_version)
    return _out(tenant)


@router.get("/admin/tenants")
def list_tenants(user: SuperAdmin, session: Db) -> Page[TenantOut]:
    return Page[TenantOut](items=[_out(t) for t in repository.list_tenants(session)])


@router.patch("/admin/tenants/{tenant_id}")
def patch_tenant(
    tenant_id: uuid.UUID,
    body: TenantPatch,
    user: SuperAdmin,
    session: Db,
    request: Request,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> TenantOut:
    tenant = service.set_tenant_status(
        session, _auth(request).audit, user.keycloak_sub, tenant_id, body.status, if_match
    )
    response.headers["ETag"] = etag(tenant.row_version)
    return _out(tenant)


def tenant_user(row: User, response: Response | None = None) -> TenantUser:
    if response is not None:
        response.headers["ETag"] = etag(row.row_version)
    return TenantUser.model_validate(row, from_attributes=True)


@router.get("/admin/users")
def list_users(user: Admin, session: Db) -> Page[TenantUser]:
    return Page[TenantUser](items=[tenant_user(u) for u in service.list_users(session, user)])


@router.post("/admin/users/invite", status_code=201)
def invite_user(
    body: UserInvite, user: Admin, session: Db, request: Request, response: Response
) -> TenantUser:
    row = service.invite_user(session, _auth(request).users, user, body)
    return tenant_user(row, response)


@router.patch("/admin/users/{user_id}")
def patch_user(
    user_id: uuid.UUID,
    body: UserPatch,
    user: Admin,
    session: Db,
    request: Request,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> TenantUser:
    row = service.update_user(session, _auth(request).users, user, user_id, body, if_match)
    return tenant_user(row, response)


@router.get("/me")
def me(user: Annotated[CurrentUser, Depends(authenticated)], session: Db) -> Me:
    row = repository.user_by_sub(session, user.keycloak_sub) if user.tenant_id else None
    tenant = session.get(Tenant, user.tenant_id) if user.tenant_id else None
    return Me(
        user=UserProfile.model_validate(row, from_attributes=True) if row else None,
        tenant=_out(tenant) if tenant else None,
        roles=sorted(user.roles & set(service.PORTAL_ROLES)),
        assigned_projects=sorted(user.assigned_project_ids),
    )
