"""`audit_log` and `notification_outbox` (TD §7.8, §14.7, §16, E1.8).

`audit_log` is append-only (C-42): one row per state transition and per security event, never
updated or deleted (a trigger enforces it, see `audit.sql`). Rows don't carry `row_version` or
`deleted_at` because they never change. `tenant_id` is NULL only for platform-level events.

`notification_outbox` is written in the same transaction as the transition and dispatched after
commit, so a rolled-back transition never notifies anyone.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.core.ids import uuid7
from sbam_api.db.base import Base, utcnow

Json = JSON().with_variant(JSONB(), "postgresql")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        # An activity retry with the same key finds the row instead of writing a second one.
        UniqueConstraint("tenant_id", "entity_type", "entity_id", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    actor_id: Mapped[str] = mapped_column(String(255))  # user id, or "SYSTEM"
    action: Mapped[str] = mapped_column(String(100))  # the event, or e.g. "access_denied"
    entity_type: Mapped[str] = mapped_column(String(64))  # machine name, or "route"
    entity_id: Mapped[str | None] = mapped_column(String(255))
    before: Mapped[dict[str, Any] | None] = mapped_column(Json)
    after: Mapped[dict[str, Any] | None] = mapped_column(Json)
    correlation_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audit_log.id"))
    type: Mapped[str] = mapped_column(String(100))  # e.g. "sub_skill.tests_passed"
    payload: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500))
