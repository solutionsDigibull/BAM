"""In-app notifications (E10.4): the `notifications` table, the outbox sink and `/notifications`."""

from sbam_api.notifications.router import router

__all__ = ["router"]
