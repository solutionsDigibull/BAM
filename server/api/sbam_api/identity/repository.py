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
from typing import Any

from sbam_contracts.api import PAGE_LIMIT_MAX
from sbam_contracts.identity import UserStatus
from sqlalchemy import func, select
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


def email_taken(session: Session, email: str) -> bool:
    found = session.scalar(select(User.id).where(func.lower(User.email) == email.lower()))
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

    def other_active_admins(self, user_id: uuid.UUID) -> int:
        # ponytail: filters roles in Python; a tenant has a handful of users, not thousands.
        rows = self.session.scalars(
            self._scoped().where(User.id != user_id, User.status == UserStatus.ACTIVE.value)
        )
        return sum(ROLE_ADMIN in row.portal_roles for row in rows)
