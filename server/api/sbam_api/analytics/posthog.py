"""Server-side PostHog capture (E16.2), fire-and-forget.

`Analytics.send` runs inside each outbox dispatch pass, after the transitions committed. It never
raises: a PostHog outage, a timeout or a bad answer only counts `posthog_events_failed_total` and is
logged; the dispatch, the notification and the transition are never held up (E16.2-T06).

Configured from the environment, the same in sbam-api and every worker: POSTHOG_HOST,
POSTHOG_API_KEY, POSTHOG_DISTINCT_ID_SALT, SBAM_ENV. No host or key = nothing is sent, as on a
laptop without the `analytics` profile.
"""

import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import cache
from typing import Any, Protocol

import httpx
from opentelemetry import metrics
from sqlalchemy.orm import Session

from sbam_api.analytics.events import build_event, event_for
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.audit.repository import previous_transition_at

log = logging.getLogger("sbam_api.analytics")

_meter = metrics.get_meter("sbam_api.analytics")  # a no-op until the process sets a MeterProvider
SENT = _meter.create_counter("posthog_events_sent", description="Product events PostHog accepted")
FAILED = _meter.create_counter("posthog_events_failed", description="Product events not delivered")

TIMEOUT_SECONDS = 2.0


class Transport(Protocol):
    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response: ...


class PostHogClient:
    def __init__(self, host: str, api_key: str, transport: Transport | None = None) -> None:
        self.url = f"{host.rstrip('/')}/batch/"
        self.api_key = api_key
        self.transport: Transport = transport or httpx.Client()

    def send(self, batch: list[dict[str, Any]]) -> None:
        response = self.transport.post(
            self.url, json={"api_key": self.api_key, "batch": batch}, timeout=TIMEOUT_SECONDS
        )
        response.raise_for_status()


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)  # SQLite (tests) drops the zone


class Analytics:
    def __init__(self, client: PostHogClient | None, environment: str, salt: str) -> None:
        self.client = client
        self.environment = environment
        self.salt = salt

    def send(self, session: Session, rows: Sequence[NotificationOutbox]) -> int:
        """Send the product events among `rows` in one batch; returns how many were accepted."""
        if self.client is None:
            return 0
        try:
            batch = [e for e in (self._event(session, r) for r in rows) if e is not None]
            if not batch:
                return 0
            self.client.send(batch)
        except Exception as exc:  # never let analytics hold up a dispatch or a transition
            FAILED.add(len(rows))
            log.warning("posthog capture failed", extra={"exc_type": type(exc).__name__})
            return 0
        SENT.add(len(batch))
        return len(batch)

    def _event(self, session: Session, row: NotificationOutbox) -> dict[str, Any] | None:
        if event_for(row.type) is None:
            return None
        audit = session.get(AuditLog, row.audit_id)
        if audit is None:
            return None
        previous = previous_transition_at(session, audit)
        return build_event(
            row_id=row.id,
            outbox_type=row.type,
            tenant_id=row.tenant_id,
            payload=row.payload,
            actor_id=audit.actor_id,
            at=_aware(audit.at),
            previous_at=_aware(previous) if previous is not None else None,
            environment=self.environment,
            salt=self.salt,
        )


@cache
def default_analytics() -> Analytics:
    """From the environment, once per process; settings validation has already checked it."""
    host = os.environ.get("POSTHOG_HOST", "").strip()
    key = os.environ.get("POSTHOG_API_KEY", "").strip()
    client = PostHogClient(host, key) if host and key else None
    return Analytics(
        client,
        environment=os.environ.get("SBAM_ENV", "development"),
        salt=os.environ.get("POSTHOG_DISTINCT_ID_SALT", ""),
    )
