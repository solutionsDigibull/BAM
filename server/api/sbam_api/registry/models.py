"""Runtime registry tables (TD §7.3, E12.1): what each `skill-runtime` image digest contains.

Platform-wide, not tenant data: every tenant's Sub-Skills run on the same certified image, so these
tables have no `tenant_id` (a documented exception to TD §7.1). Rows are written only by the sync
(`python -m sbam_api.registry.sync`), never by the API.

One row per image digest; exactly one is active (partial unique index). Older registries stay, so
frozen versions can still be checked against the image they were certified on.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from sbam_api.core.ids import uuid7
from sbam_api.db.base import Base, utcnow

Json = JSON().with_variant(JSONB(), "postgresql")
ONLY_ACTIVE: ColumnElement[bool] = literal_column("is_active")


class RuntimeRegistryRow(Base):
    __tablename__ = "runtime_registries"
    __table_args__ = (
        Index(
            "ix_runtime_registries_one_active",
            "is_active",
            unique=True,
            postgresql_where=ONLY_ACTIVE,
            sqlite_where=ONLY_ACTIVE,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    image_digest: Mapped[str] = mapped_column(String(71), unique=True)  # sha256:<64 hex>
    python_version: Mapped[str] = mapped_column(String(20))
    content_sha256: Mapped[str] = mapped_column(String(71))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class RuntimeRegistryPackageRow(Base):
    __tablename__ = "runtime_registry_packages"
    __table_args__ = (UniqueConstraint("registry_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    registry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runtime_registries.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))  # PEP 503 normalised
    version: Mapped[str] = mapped_column(String(100))
    top_level_modules: Mapped[list[str]] = mapped_column(Json)
    allowed: Mapped[bool] = mapped_column(Boolean)  # in requirements.in (E3.12)
