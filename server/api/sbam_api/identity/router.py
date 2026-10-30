"""Identity API: super-admin tenants and the caller's own profile (E2.3), tenant users (E2.4).

`/admin/tenants` is platform-level (super-admin, no tenant); an sbam-admin gets 403 WRONG_PORTAL.
`/admin/users` and `/admin/audit` are the caller's tenant only (another tenant's user: 404).
`/me` answers for any signed-in user.
"""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sbam_contracts.api import Page
from sbam_contracts.audit import AuditEntry
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
from sbam_api.identity import paging, repository, service
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
def list_tenants(
    user: SuperAdmin,
    session: Db,
    request: Request,
    limit: str | None = None,
    cursor: str | None = None,
) -> Page[TenantOut]:
    """`?limit=1..100 (25)&cursor=`. Platform-wide, so the cursor is signed for the caller."""
    secret = request.app.state.settings.cursor_secret.get_secret_value().encode()
    scope = f"tenants:{user.keycloak_sub}"
    size, after = paging.read_page(cursor, limit, scope, secret)
    rows, next_cursor = paging.cut(
        repository.list_tenants(session, after, size), size, scope, secret
    )
    return Page[TenantOut](items=[_out(t) for t in rows], next_cursor=next_cursor)


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
def list_users(
    user: Admin,
    session: Db,
    request: Request,
    limit: str | None = None,
    cursor: str | None = None,
    role: str | None = None,
    active: str | None = None,
) -> Page[TenantUser]:
    """`?limit=1..100 (25)&cursor=&role=sbam-admin|sbam-fde&active=true|false`."""
    secret = request.app.state.settings.cursor_secret.get_secret_value().encode()
    rows, next_cursor = service.list_users(
        session, user, secret, limit=limit, cursor=cursor, role=role, active=active
    )
    return Page[TenantUser](items=[tenant_user(u) for u in rows], next_cursor=next_cursor)


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


@router.get("/admin/audit")
def list_audit(
    user: Admin,
    session: Db,
    request: Request,
    limit: str | None = None,
    cursor: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    start: Annotated[str | None, Query(alias="from")] = None,
    end: Annotated[str | None, Query(alias="to")] = None,
) -> Page[AuditEntry]:
    """`?entity_type=&entity_id=&actor=&from=&to=&limit=1..100 (25)&cursor=`, newest first."""
    secret = request.app.state.settings.cursor_secret.get_secret_value().encode()
    rows, next_cursor = service.list_audit(
        session,
        user,
        secret,
        limit=limit,
        cursor=cursor,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor,
        start=start,
        end=end,
    )
    items = [
        AuditEntry(
            id=r.id,
            action=r.action,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            actor_id=r.actor_id,
            before=r.before,
            after=r.after,
            correlation_id=r.correlation_id,
            created_at=r.at,
        )
        for r in rows
    ]
    return Page[AuditEntry](items=items, next_cursor=next_cursor)


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
