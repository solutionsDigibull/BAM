"""Identity queries (E2.3). The functions are not tenant-scoped on purpose; only the identity module
uses them:

* `tenants` is the platform table (super-admin); a tenant is the scope, it has no `tenant_id`.
* The mirror looks a user up by Keycloak subject before the tenant is trusted, then checks that
  the row's tenant matches the token's (TENANT_MISMATCH otherwise).
* An email is unique across the platform, so the invite checks every tenant.

`UserRepository` is the tenant-scoped one the admin user APIs use (E2.4).
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sbam_contracts.api import PAGE_LIMIT_MAX
from sbam_contracts.identity import UserStatus
from sqlalchemy import ColumnElement, String, and_, cast, func, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from sbam_api.core.auth import ROLE_ADMIN
from sbam_api.db.repository import TenantScopedRepository
from sbam_api.identity.models import Tenant, User


def tenant_name_taken(session: Session, name: str) -> bool:
    found = session.scalar(select(Tenant.id).where(func.lower(Tenant.name) == name.lower()))
    return found is not None


def list_tenants(session: Session) -> list[Tenant]:
    # ponytail: one page of 100, oldest first; cursor paging when a platform has more tenants.
    return list(session.scalars(select(Tenant).order_by(Tenant.id).limit(PAGE_LIMIT_MAX)))


def user_by_sub(session: Session, keycloak_sub: str) -> User | None:
    return session.scalars(select(User).where(User.keycloak_sub == keycloak_sub)).one_or_none()


def insert_user_if_new(session: Session, values: dict[str, Any]) -> bool:
    """`INSERT … ON CONFLICT (keycloak_sub) DO NOTHING`; True when this call created the row."""
    dialect = postgresql if session.get_bind().dialect.name == "postgresql" else sqlite
    stmt = (
        dialect.insert(User)
        .values(row_version=1, **values)
        .on_conflict_do_nothing(index_elements=["keycloak_sub"])
    )
    return bool(session.execute(stmt).rowcount)  # type: ignore[attr-defined]


def update_user_if(
    session: Session,
    row: User,
    values: dict[str, Any],
    when: ColumnElement[bool] | None = None,
) -> bool:
    """One atomic `UPDATE users SET <values>, row_version + 1 WHERE id = :id AND <when>`, where
    `when` defaults to "some value differs" (`IS DISTINCT FROM`). True when this call changed the
    row. Parallel requests never fail on `row_version` (#429): in PostgreSQL a second UPDATE waits
    for the first's row lock, re-checks `when` against the committed row and changes nothing, so
    whatever follows a change (an audit event) happens exactly once."""
    if when is None:
        when = or_(*(getattr(User, column).is_distinct_from(v) for column, v in values.items()))
    stmt = (
        update(User)
        .where(User.id == row.id, User.tenant_id == row.tenant_id, when)
        .values(row_version=User.row_version + 1, **values)
        .execution_options(synchronize_session=False)
    )
    return bool(session.execute(stmt).rowcount)  # type: ignore[attr-defined]


def email_in_tenant(session: Session, tenant_id: uuid.UUID, email: str) -> bool:
    """True when the caller's own tenant already has this address. Never look across tenants:
    the answer to an invite must not say whether another tenant owns the address (A-05)."""
    found = session.scalar(
        select(User.id).where(User.tenant_id == tenant_id, func.lower(User.email) == email.lower())
    )
    return found is not None


def lock_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """`SELECT … FOR UPDATE` on the tenant row: role changes in one tenant run one at a time, so
    two admins can't each demote the other and leave no admin."""
    session.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())


class UserRepository(TenantScopedRepository[User]):
    model = User

    def among(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, User]:
        rows = self.session.scalars(self._scoped().where(User.id.in_(list(ids))))
        return {row.id: row for row in rows}

    def page(
        self,
        *,
        after: tuple[datetime, uuid.UUID] | None,
        limit: int,
        role: str | None,
        active: bool | None,
    ) -> list[User]:
        """Oldest first, after the `(created_at, id)` of the previous page's last row. Ids are
        Keycloak subjects (random UUIDs), so `id` only breaks ties. `limit + 1` rows tell the
        caller whether another page exists."""
        query = self._scoped()
        if after is not None:
            created, last = after
            query = query.where(
                or_(User.created_at > created, and_(User.created_at == created, User.id > last))
            )
        if role is not None:
            query = query.where(self._has_role(role))
        if active is not None:
            is_active = User.status == UserStatus.ACTIVE.value
            query = query.where(is_active if active else ~is_active)
        ordered = query.order_by(User.created_at, User.id)
        return list(self.session.scalars(ordered.limit(limit + 1)))

    def _has_role(self, role: str) -> ColumnElement[bool]:
        """`role` is one of the fixed portal roles (validated by the caller)."""
        if self.session.get_bind().dialect.name == "postgresql":
            return User.portal_roles.contains([role])
        return cast(User.portal_roles, String).like(f'%"{role}"%')  # SQLite: JSON text

    def other_active_admins(self, user_id: uuid.UUID) -> int:
        # ponytail: filters roles in Python; a tenant has a handful of users, not thousands.
        rows = self.session.scalars(
            self._scoped().where(User.id != user_id, User.status == UserStatus.ACTIVE.value)
        )
        return sum(ROLE_ADMIN in row.portal_roles for row in rows)
