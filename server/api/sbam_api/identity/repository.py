"""Identity queries (E2.3). Not tenant-scoped on purpose; only the identity module uses them:

* `tenants` is the platform table (super-admin); a tenant is the scope, it has no `tenant_id`.
* The mirror looks a user up by Keycloak subject before the tenant is trusted, then checks that
  the row's tenant matches the token's (TENANT_MISMATCH otherwise).
"""

from typing import Any

from sbam_contracts.api import PAGE_LIMIT_MAX
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

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
