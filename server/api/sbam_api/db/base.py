"""Declarative base and table conventions (TD §7.1).

Every table lives in schema `sbam`, has a UUIDv7 key and `tenant_id`, and carries created/updated
timestamps, `row_version` (optimistic locking, = ETag) and `deleted_at` (soft delete).
Project-owned tables add `project_id`. Status columns are text + a CHECK built from the Python enum.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, MetaData, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from sbam_api.core.ids import uuid7

SCHEMA = "sbam"
# Stable constraint names, so autogenerate diffs and downgrades refer to the same objects.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


class TenantOwned:
    """Columns every SBAM table has. Mix into a declarative base: `class X(TenantOwned, Base)`."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(
        Integer, CheckConstraint("row_version >= 1", name="row_version_positive")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805 - SQLAlchemy declared_attr
        # SQLAlchemy sets 1 on insert, adds `WHERE row_version = :loaded` to every UPDATE and
        # increments it; zero rows matched raises StaleDataError (→ StaleRowVersion, 412).
        return {"version_id_col": cls.row_version}


class ProjectOwned(TenantOwned):
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)


def status_check(values: type[StrEnum] | Iterable[str], column: str = "status") -> CheckConstraint:
    """`CHECK (status IN (...))` from an enum or `state_machines.check_constraint_values(...)`."""
    allowed = [v.value for v in values] if isinstance(values, type) else list(values)
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in allowed)
    return CheckConstraint(f"{column} IN ({quoted})", name=f"{column}_valid")
