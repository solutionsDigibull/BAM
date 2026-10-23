"""Queries on `audit_log` and `notification_outbox`. Audit rows are only ever inserted or read."""

import uuid
from datetime import datetime
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


def search(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    before_id: uuid.UUID | None = None,
    limit: int,
) -> list[AuditLog]:
    """The tenant's rows, newest first (uuid7 ids sort by time), for `GET /admin/audit` (E2.8).

    The tenant is required: platform rows (tenant_id NULL) and other tenants' rows never match.
    `limit + 1` rows tell the caller whether another page exists.
    """
    query = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    for column, value in (
        (AuditLog.entity_type, entity_type),
        (AuditLog.entity_id, entity_id),
        (AuditLog.actor_id, actor_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if start is not None:
        query = query.where(AuditLog.at >= start)
    if end is not None:
        query = query.where(AuditLog.at <= end)
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    return list(session.scalars(query.order_by(AuditLog.id.desc()).limit(limit + 1)))


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


def previous_transition_at(session: Session, audit: AuditLog) -> datetime | None:
    """When the same entity last changed before `audit`: the time it spent in that stage (E16.2)."""
    query = (
        select(AuditLog.at)
        .where(
            AuditLog.tenant_id == audit.tenant_id,
            AuditLog.entity_type == audit.entity_type,
            AuditLog.entity_id == audit.entity_id,
            AuditLog.at < audit.at,
        )
        .order_by(AuditLog.at.desc())
        .limit(1)
    )
    return session.scalar(query)
