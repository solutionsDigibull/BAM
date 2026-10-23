"""Notification outbox dispatch (TD §14.7, §17).

Transitions write an outbox row in their own transaction; `dispatch_pending` runs only after the
commit, so a rolled-back transition never notifies. A sink failure leaves the row pending (attempts
+1, last_error = exception type) for the next pass; it never undoes the transition.

The sink is a port: the in-app `notifications` table and its API (TD §8.13, §17) plug in later.
Each pass also hands its rows to analytics (E16.2), fire-and-forget: PostHog never holds it up.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from sbam_api.audit.repository import pending_outbox
from sbam_api.db.base import utcnow

if TYPE_CHECKING:
    from sbam_api.analytics import Analytics

log = logging.getLogger("sbam_api.notifications")


@dataclass(frozen=True)
class OutboxItem:
    id: UUID
    tenant_id: UUID
    type: str
    payload: dict[str, Any]


class NotificationSink(Protocol):
    def send(self, item: OutboxItem) -> None: ...


class LogSink:
    """Default until the notifications module lands: one log line per notification."""

    def send(self, item: OutboxItem) -> None:
        log.info("notification %s for tenant %s", item.type, item.tenant_id)


def dispatch_pending(
    sessions: sessionmaker[Session],
    sink: NotificationSink,
    limit: int = 100,
    analytics: "Analytics | None" = None,
) -> int:
    """Send pending notifications; returns how many were sent."""
    from sbam_api.analytics import default_analytics

    sent = 0
    with sessions() as session:
        rows = pending_outbox(session, limit)
        (analytics or default_analytics()).send(session, rows)  # never raises
        for row in rows:
            item = OutboxItem(row.id, row.tenant_id, row.type, row.payload)
            try:
                sink.send(item)
            except Exception as exc:  # retried on the next pass
                row.attempts += 1
                row.last_error = type(exc).__name__
                continue
            row.attempts += 1
            row.dispatched_at = utcnow()
            sent += 1
        session.commit()
    return sent
