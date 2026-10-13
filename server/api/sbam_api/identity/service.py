"""Tenants and the users mirror (TD §8.2, E2.3). The router only maps HTTP onto these.

Tenant creation is atomic across Postgres and Keycloak: the name is checked, the organization is
created, then the row is inserted. A Keycloak failure leaves no row; an insert that loses a race on
the unique name (or fails otherwise) deletes the organization again.

The mirror runs on every tenant user's request (`core.auth.current_user`): the tenant must exist and
be ACTIVE, the user's row is created on first sight (`ON CONFLICT DO NOTHING`, so two tabs make one
row) and refreshed from the token when something changed. A user never moves to another tenant.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sbam_contracts.api import ErrorCode
from sbam_contracts.identity import TenantStatus
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from sbam_api.core.auth import (
    ROLE_ADMIN,
    ROLE_FDE,
    ROLE_SUPER_ADMIN,
    CurrentUser,
    OrgDirectory,
    SecurityAudit,
)
from sbam_api.core.conventions import ApiError, check_if_match, not_found
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import StaleRowVersion
from sbam_api.identity import repository
from sbam_api.identity.models import Tenant, User

PORTAL_ROLES = (ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_FDE)


def duplicate_name() -> ApiError:
    return ApiError(409, ErrorCode.DUPLICATE_NAME, "A tenant with this name already exists")


def tenant_suspended() -> ApiError:
    return ApiError(403, ErrorCode.TENANT_SUSPENDED, "This tenant is suspended")


def tenant_mismatch() -> ApiError:
    return ApiError(403, ErrorCode.TENANT_MISMATCH, "Your account does not belong to this tenant")


# --- Tenants (super-admin) -------------------------------------------------------------------


def create_tenant(
    session: Session, orgs: OrgDirectory, audit: SecurityAudit, actor: str, name: str
) -> Tenant:
    if repository.tenant_name_taken(session, name):
        raise duplicate_name()  # the unique index still decides a race, below
    tenant_id = uuid7()
    org_id = orgs.create(tenant_id, name)  # 503 before any row exists
    tenant = Tenant(id=tenant_id, name=name, keycloak_org_id=org_id, created_by=actor)
    session.add(tenant)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        orgs.delete(org_id)
        raise duplicate_name() from None
    except Exception:
        session.rollback()
        orgs.delete(org_id)
        raise
    audit.event(
        actor=actor,
        tenant_id=tenant.id,
        action="TENANT_CREATED",
        entity="tenant",
        entity_id=str(tenant.id),
        after={"name": name, "keycloak_org_id": org_id},
    )
    return tenant


def set_tenant_status(
    session: Session,
    audit: SecurityAudit,
    actor: str,
    tenant_id: uuid.UUID,
    status: TenantStatus,
    if_match: str | None,
) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise not_found()
    check_if_match(if_match, tenant.row_version)
    before = tenant.status
    tenant.status = status.value
    try:
        session.commit()
    except StaleDataError:
        raise StaleRowVersion() from None
    if before != status.value:
        audit.event(
            actor=actor,
            tenant_id=tenant.id,
            action="TENANT_STATUS_CHANGED",
            entity="tenant",
            entity_id=str(tenant.id),
            before={"status": before},
            after={"status": status.value},
        )
    return tenant


# --- The users mirror ------------------------------------------------------------------------


class SqlUserDirectory:
    def __init__(self, sessions: Callable[[], sessionmaker[Session]], audit: SecurityAudit) -> None:
        self.sessions = sessions
        self.audit = audit

    def sync(self, user: CurrentUser) -> None:
        if user.tenant_id is None:  # current_user only syncs tenant users
            return
        with self.sessions()() as session:
            tenant = session.get(Tenant, user.tenant_id)
            if tenant is None:  # an organization SBAM doesn't know (or an orphan one)
                self._deny(user, "unknown tenant")
                raise tenant_mismatch()
            if tenant.status != TenantStatus.ACTIVE:
                raise tenant_suspended()
            row = repository.user_by_sub(session, user.keycloak_sub)
            created = False
            if row is None:
                created = self._insert(session, user)
                row = repository.user_by_sub(session, user.keycloak_sub)
            if row is None or row.tenant_id != tenant.id:  # never move a user between tenants
                self._deny(user, "tenant mismatch")
                raise tenant_mismatch()
            self._refresh(session, row, user, created)

    def _insert(self, session: Session, user: CurrentUser) -> bool:
        """Create the row if the subject is new; True when this request created it."""
        created = repository.insert_user_if_new(
            session,
            {
                "id": user.user_id,
                "tenant_id": user.tenant_id,
                "keycloak_sub": user.keycloak_sub,
                "email": user.email,
                "display_name": user.display_name,
                "portal_roles": sorted(user.roles & set(PORTAL_ROLES)),
                "last_login_at": datetime.now(UTC),
                "last_session_id": user.session_id,
                "created_by": user.keycloak_sub,
            },
        )
        session.commit()
        if created:
            self._login(user, first=True)
        return created

    def _refresh(self, session: Session, row: User, user: CurrentUser, created: bool) -> None:
        roles = sorted(user.roles & set(PORTAL_ROLES))
        new_login = (
            not created and user.session_id is not None and user.session_id != row.last_session_id
        )
        old_email = row.email
        changed = False
        for column, value in (
            ("email", user.email),
            ("display_name", user.display_name),
            ("portal_roles", roles),
        ):
            if getattr(row, column) != value:
                setattr(row, column, value)
                changed = True
        if new_login:
            row.last_login_at, row.last_session_id = datetime.now(UTC), user.session_id
        if not (changed or new_login):
            return  # the usual request: nothing to write
        session.commit()
        if new_login:
            self._login(user, first=False)
        if old_email != row.email:  # the old address stays in the audit trail
            self.audit.event(
                actor=user.keycloak_sub,
                tenant_id=row.tenant_id,
                action="USER_EMAIL_CHANGED",
                entity="user",
                entity_id=str(row.id),
                before={"email": old_email},
                after={"email": row.email},
            )

    def _login(self, user: CurrentUser, *, first: bool) -> None:
        self.audit.event(
            actor=user.keycloak_sub,
            tenant_id=user.tenant_id,
            action="LOGIN",
            entity="user",
            entity_id=str(user.user_id),
            after={"first_login": first},
        )

    def _deny(self, user: CurrentUser, reason: str) -> None:
        self.audit.denied(actor=user, route="login", target=str(user.tenant_id), reason=reason)
