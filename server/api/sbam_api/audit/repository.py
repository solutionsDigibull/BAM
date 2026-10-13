"""Queries on `audit_log` and `notification_outbox`. Audit rows are only ever inserted or read."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbam_api.audit.models import AuditLog, NotificationOutbox


def record(
    session: Session,
    *,
    tenant_id: uuid.UUID | None,
    actor_id: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    correlation_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> AuditLog:
    if not correlation_id:
        raise ValueError("audit rows need a correlation_id")
    row = AuditLog(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    session.add(row)
    session.flush()
    return row


def find_by_key(
    session: Session, tenant_id: uuid.UUID, entity_type: str, entity_id: str, key: str
) -> AuditLog | None:
    query = select(AuditLog).where(
        AuditLog.tenant_id == tenant_id,
        AuditLog.entity_type == entity_type,
        AuditLog.entity_id == entity_id,
        AuditLog.idempotency_key == key,
    )
    return session.scalars(query).one_or_none()


def entity_history(
    session: Session, tenant_id: uuid.UUID, entity_type: str, entity_id: str
) -> list[AuditLog]:
    """An entity's audit trail, oldest first (tenant-scoped)."""
    query = (
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == entity_id,
        )
        .order_by(AuditLog.id)
    )
    return list(session.scalars(query))


def enqueue(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    audit_id: uuid.UUID,
    type: str,
    payload: dict[str, Any],
) -> NotificationOutbox:
    row = NotificationOutbox(tenant_id=tenant_id, audit_id=audit_id, type=type, payload=payload)
    session.add(row)
    session.flush()
    return row


def pending_outbox(session: Session, limit: int) -> list[NotificationOutbox]:
    """Undispatched rows, oldest first, locked so two dispatchers never send the same row."""
    query = (
        select(NotificationOutbox)
        .where(NotificationOutbox.dispatched_at.is_(None))
        .order_by(NotificationOutbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(query))
