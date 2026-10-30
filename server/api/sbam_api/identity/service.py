"""Tenants and the users mirror (TD §8.2, E2.3). The router only maps HTTP onto these.

Tenant creation is atomic across Postgres and Keycloak: the name is checked, the organization is
created, then the row is inserted. A Keycloak failure leaves no row; an insert that loses a race on
the unique name (or fails otherwise) deletes the organization again.

The mirror runs on every tenant user's request (`core.auth.current_user`): the tenant must exist and
be ACTIVE, the user's row is created on first sight (`ON CONFLICT DO NOTHING`, so two tabs make one
row) and refreshed from the token when something changed. A user never moves to another tenant.
`portal_roles` is taken from the token only on a new login: an admin's role change (E2.4) is
written here first, and a still-valid older token must not write the old roles back. An INVITED
user becomes ACTIVE on first sight; a DEACTIVATED one gets 401 even with an unexpired token.
"""

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.identity import TenantStatus, UserInvite, UserPatch, UserStatus
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from sbam_api.audit import repository as audit_repository
from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import record
from sbam_api.core.auth import (
    ROLE_ADMIN,
    ROLE_FDE,
    ROLE_SUPER_ADMIN,
    CurrentUser,
    OrgDirectory,
    SecurityAudit,
    UserAdmin,
    unauthenticated,
)
from sbam_api.core.conventions import (
    ApiError,
    check_if_match,
    decode_cursor,
    encode_cursor,
    not_found,
    parse_limit,
)
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import StaleRowVersion
from sbam_api.identity import repository
from sbam_api.identity.idp_sync import converge_user
from sbam_api.identity.keycloak_admin import user_exists
from sbam_api.identity.models import Tenant, User
from sbam_api.identity.repository import UserRepository

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


# --- Tenant users (sbam-admin, E2.4) -----------------------------------------------------------
#
# The row is the source of truth. A role change or deactivation is committed with a pending mark
# (a stale row is 412 before anything is saved), then Keycloak is converged outside the tenant
# lock and its sessions revoked. If Keycloak is down the change stays saved and pending, a relay
# retries it (A-13), and the DB status check already refuses a deactivated user.


def last_admin() -> ApiError:
    return ApiError(409, ErrorCode.LAST_ADMIN, "The tenant must keep at least one active admin")


LIST_ROLES = (ROLE_ADMIN, ROLE_FDE)
AUDIT_MAX_RANGE = timedelta(days=92)
ENTITY_TYPE = re.compile(r"^[a-z_]{1,64}$")


def bad_param(path: str, message: str, code: ErrorCode = ErrorCode.VALIDATION_ERROR) -> ApiError:
    """400 on a query parameter, the same shape as `parse_limit`'s."""
    return ApiError(400, code, "Invalid request", errors=[FieldError(path=path, message=message)])


def list_users(
    session: Session,
    actor: CurrentUser,
    secret: bytes,
    *,
    limit: str | None,
    cursor: str | None,
    role: str | None,
    active: str | None,
) -> tuple[list[User], str | None]:
    """One page of the tenant's users, oldest first, and the cursor of the next page (or None).

    The cursor is signed for this tenant: edited, forged or another tenant's -> INVALID_CURSOR.
    """
    size = parse_limit(limit)
    if role is not None and role not in LIST_ROLES:
        raise bad_param("role", f"must be one of {', '.join(LIST_ROLES)}")
    if active not in (None, "true", "false"):
        raise bad_param("active", "must be true or false")
    tenant = str(actor.tenant_id)
    after = None
    if cursor:
        position = decode_cursor(cursor, tenant, secret)
        try:
            after = (
                datetime.fromisoformat(str(position["created"])),
                uuid.UUID(str(position["id"])),
            )
        except (KeyError, ValueError):
            bad = bad_param("cursor", "invalid or expired cursor", ErrorCode.INVALID_CURSOR)
            raise bad from None
    rows = UserRepository(session, actor.context()).page(
        after=after, limit=size, role=role, active=None if active is None else active == "true"
    )
    if len(rows) <= size:
        return rows, None
    rows = rows[:size]
    last = rows[-1]
    position = {"created": last.created_at.isoformat(), "id": str(last.id)}
    return rows, encode_cursor(position, tenant, secret)


def invite_user(session: Session, users: UserAdmin, actor: CurrentUser, body: UserInvite) -> User:
    """Invite `body.email` into the caller's tenant.

    409 USER_EXISTS only when the caller's own tenant has the address (the admin can list those
    users anyway). An address that exists elsewhere in the Keycloak realm (one person, one tenant)
    gets the same 201 a free address gets, and nothing is created: any other answer would tell one
    tenant's admin who is registered in another (A-05). The skipped invite is audited."""
    tenant = session.get(Tenant, actor.tenant_id)
    if tenant is None:
        raise not_found()
    if repository.email_in_tenant(session, tenant.id, body.email):
        raise user_exists()
    roles = sorted(map(str, body.roles))
    try:
        kc_id = users.invite(tenant.id, tenant.keycloak_org_id, body.email, roles)
    except ApiError as exc:
        if exc.code != ErrorCode.USER_EXISTS:
            raise
        return _skipped_invite(session, actor, body, roles)
    try:
        row = UserRepository(session, actor.context()).add(
            User(
                id=uuid.UUID(kc_id),
                keycloak_sub=kc_id,
                email=body.email,
                portal_roles=roles,
                status=UserStatus.INVITED.value,
                created_by=actor.keycloak_sub,
            )
        )
        _audit(session, actor, "USER_INVITED", row, None, {"email": body.email, "roles": roles})
        session.commit()
    except Exception as exc:
        session.rollback()
        users.delete(kc_id)
        if isinstance(exc, IntegrityError):
            raise user_exists() from None
        raise
    return row


def _skipped_invite(
    session: Session, actor: CurrentUser, body: UserInvite, roles: list[str]
) -> User:
    """The answer for an address another tenant (or an orphan Keycloak account) already holds: a
    transient row shaped like a fresh invite, never added to the session, so its id resolves to
    404 like any unknown id. Only the audit trail records that the invite did nothing."""
    record(
        session,
        tenant_id=actor.tenant_id,
        actor_id=str(actor.user_id),
        action="USER_INVITE_SKIPPED",
        entity_type="user",
        entity_id=None,
        before=None,
        after={"email": body.email, "roles": roles},
        correlation_id=correlation_id_var.get() or "unknown",
    )
    session.commit()
    kc_id = uuid7()
    now = datetime.now(UTC)
    return User(
        id=kc_id,
        keycloak_sub=str(kc_id),
        tenant_id=actor.tenant_id,
        email=body.email,
        portal_roles=roles,
        status=UserStatus.INVITED.value,
        created_at=now,
        updated_at=now,
        row_version=1,
    )


def update_user(
    session: Session,
    users: UserAdmin,
    actor: CurrentUser,
    user_id: uuid.UUID,
    body: UserPatch,
    if_match: str | None,
) -> User:
    repo = UserRepository(session, actor.context())
    repository.lock_tenant(session, repo.ctx.tenant_id)
    row = repo.get(user_id)
    if row is None:
        raise not_found()
    check_if_match(if_match, row.row_version)
    roles = sorted(map(str, body.roles)) if body.roles is not None else list(row.portal_roles)
    status = row.status
    if body.active is False:
        status = UserStatus.DEACTIVATED.value
    elif body.active and row.status == UserStatus.DEACTIVATED:
        status = UserStatus.ACTIVE.value
    roles_changed, status_changed = roles != row.portal_roles, status != row.status
    if not (roles_changed or status_changed):
        return row
    was_admin = row.status == UserStatus.ACTIVE and ROLE_ADMIN in row.portal_roles
    stays_admin = status == UserStatus.ACTIVE and ROLE_ADMIN in roles
    if was_admin and not stays_admin and not repo.other_active_admins(row.id):
        raise last_admin()

    old_roles, old_status = list(row.portal_roles), row.status
    row.portal_roles, row.status = roles, status
    if row.idp_sync_pending_at is None:
        row.idp_synced_roles = old_roles  # what Keycloak holds until it is told
    row.idp_sync_pending_at = datetime.now(UTC)
    repo.update(row, row.row_version)  # flushed, not committed: a concurrent writer -> 412 here
    if roles_changed:
        _audit(session, actor, "ROLE_CHANGED", row, {"roles": old_roles}, {"roles": roles})
    if status_changed:
        action = "USER_DEACTIVATED" if status == UserStatus.DEACTIVATED else "USER_REACTIVATED"
        _audit(session, actor, action, row, {"status": old_status}, {"status": status})
    session.commit()  # the tenant lock ends here, before any Keycloak call
    converge_user(sessionmaker(bind=session.get_bind()), users, row.id)  # False: the relay retries
    return row


def _audit(
    session: Session,
    actor: CurrentUser,
    action: str,
    row: User,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    record(
        session,
        tenant_id=row.tenant_id,
        actor_id=str(actor.user_id),
        action=action,
        entity_type="user",
        entity_id=str(row.id),
        before=before,
        after=after,
        correlation_id=correlation_id_var.get() or "unknown",
    )


# --- Audit log (admin, E2.8) -----------------------------------------------------------------


def list_audit(
    session: Session,
    actor: CurrentUser,
    secret: bytes,
    *,
    limit: str | None,
    cursor: str | None,
    entity_type: str | None,
    entity_id: str | None,
    actor_id: str | None,
    start: str | None,
    end: str | None,
) -> tuple[list[AuditLog], str | None]:
    """One page of the tenant's audit rows, newest first, and the next page's cursor (or None).

    `from`/`to` are ISO 8601 with a timezone, at most 92 days apart; with only one of them the
    other end is 92 days away. A bad query is 400 on the parameter (docs/api-conventions.md).
    """
    size = parse_limit(limit)
    if entity_type is not None and not ENTITY_TYPE.match(entity_type):
        raise bad_param("entity_type", "must be lowercase letters and underscores")
    for name, value in (("entity_id", entity_id), ("actor", actor_id)):
        if value is not None and not 1 <= len(value) <= 255:
            raise bad_param(name, "must be 1 to 255 characters")
    lo, hi = _instant("from", start), _instant("to", end)
    if lo and hi and lo > hi:
        raise bad_param("from", "must not be after `to`")
    if lo and hi and hi - lo > AUDIT_MAX_RANGE:
        raise bad_param("from", "the range can be at most 92 days")
    if lo and not hi:
        hi = lo + AUDIT_MAX_RANGE
    elif hi and not lo:
        lo = hi - AUDIT_MAX_RANGE
    tenant = str(actor.tenant_id)
    before_id = None
    if cursor:
        position = decode_cursor(cursor, tenant, secret)
        try:
            before_id = uuid.UUID(str(position["before"]))
        except (KeyError, ValueError):
            bad = bad_param("cursor", "invalid or expired cursor", ErrorCode.INVALID_CURSOR)
            raise bad from None
    rows = audit_repository.search(
        session,
        actor.context().tenant_id,  # the token's tenant, never a parameter
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        start=lo,
        end=hi,
        before_id=before_id,
        limit=size,
    )
    if len(rows) <= size:
        return rows, None
    rows = rows[:size]
    return rows, encode_cursor({"before": str(rows[-1].id)}, tenant, secret)


def _instant(name: str, raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise bad_param(name, "must be an ISO 8601 date-time") from None
    if value.tzinfo is None:
        raise bad_param(name, "must include a timezone, e.g. 2026-10-15T00:00:00Z")
    return value


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
            if row.status == UserStatus.DEACTIVATED:
                self._deny(user, "deactivated")
                raise unauthenticated()
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
        """Mirror the token onto the row. Each change is one conditional UPDATE (#429), so parallel
        first requests of a new session all succeed and only the one whose UPDATE changed the row
        writes its audit event: one LOGIN per new session, one USER_EMAIL_CHANGED per change."""
        old_email = row.email
        mirrored = {"email": user.email, "display_name": user.display_name}
        mirror = any(getattr(row, column) != value for column, value in mirrored.items())
        new_login = (
            not created and user.session_id is not None and user.session_id != row.last_session_id
        )
        invited = row.status == UserStatus.INVITED
        if not (mirror or new_login or invited):
            return  # the usual request: nothing to write, no transaction spent
        email_changed = mirror and repository.update_user_if(session, row, mirrored)
        logged_in = new_login and repository.update_user_if(
            session,
            row,
            {
                "last_session_id": user.session_id,
                "last_login_at": datetime.now(UTC),
                "portal_roles": sorted(user.roles & set(PORTAL_ROLES)),
            },
            User.last_session_id.is_distinct_from(user.session_id),
        )
        if invited:
            repository.update_user_if(
                session, row, {"status": UserStatus.ACTIVE.value}, User.status == UserStatus.INVITED
            )
        session.commit()
        if logged_in:
            self._login(user, first=False)
        if email_changed and old_email != user.email:  # the old address stays in the audit trail
            self.audit.event(
                actor=user.keycloak_sub,
                tenant_id=row.tenant_id,
                action="USER_EMAIL_CHANGED",
                entity="user",
                entity_id=str(row.id),
                before={"email": old_email},
                after={"email": user.email},
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
