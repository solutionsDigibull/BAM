"""Product and eval analytics (TD §17.2, E16.2): PostHog events built from the notification outbox.

Every state transition already writes an outbox row in its own transaction (state_machine), and the
outbox is sent only after commit; `audit.outbox.dispatch_pending` hands each row to `capture` as
well. So a rolled-back transition emits nothing, an activity retry (replayed by its idempotency key)
writes no second row, and PostHog dedupes a re-sent row by its uuid, the outbox row id.
"""

from sbam_api.analytics.events import AnalyticsEvent, build_event, event_for
from sbam_api.analytics.posthog import Analytics, PostHogClient, default_analytics

__all__ = [
    "Analytics",
    "AnalyticsEvent",
    "PostHogClient",
    "build_event",
    "default_analytics",
    "event_for",
]
