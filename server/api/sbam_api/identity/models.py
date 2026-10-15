"""`tenants` (platform-level, super-admin) and `users` (the Keycloak mirror) (TD §7, §8.2, E2.3)."""

import uuid
from datetime import datetime

from sbam_contracts.identity import TenantStatus, UserStatus
from sqlalchemy import JSON, DateTime, Index, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.core.ids import uuid7
from sbam_api.db.base import Base, TenantOwned, status_check, utcnow


class Tenant(Base):
    """The one table without `tenant_id`: a tenant is the scope. Only super-admin reads it."""

    __tablename__ = "tenants"
    __table_args__ = (status_check(TenantStatus),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(120))
    keycloak_org_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default=TenantStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer)
    __mapper_args__ = {"version_id_col": row_version}  # noqa: RUF012 - stale write: 412


# Names are unique ignoring case; the contract already trimmed them.
Index("uq_tenants_name_ci", func.lower(Tenant.name), unique=True)


class User(TenantOwned, Base):
    """One row per Keycloak subject, refreshed from the token. `id` is the subject's UUID.

    An invited user's row exists before their first login (status INVITED)."""

    __tablename__ = "users"
    __table_args__ = (status_check(UserStatus),)

    keycloak_sub: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str | None] = mapped_column(String(254))
    display_name: Mapped[str | None] = mapped_column(String(255))
    portal_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)).with_variant(JSON(), "sqlite"), default=list
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_session_id: Mapped[str | None] = mapped_column(String(64))  # Keycloak `sid`
    status: Mapped[str] = mapped_column(String(20), default=UserStatus.ACTIVE.value)
