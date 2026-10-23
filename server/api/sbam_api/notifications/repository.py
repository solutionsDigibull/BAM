"""Tenant- and user-scoped notification queries (E10.4).

A notification belongs to one user. Every query here filters by the caller's tenant (base
repository) and by `user_id`, so another user's row, another tenant's row and a missing row all
look the same: not found.
"""

import uuid
from typing import Any

from sbam_contracts.api import PAGE_LIMIT_MAX
from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite

from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantScopedRepository
from sbam_api.notifications.models import Notification


class NotificationRepository(TenantScopedRepository[Notification]):
    model = Notification

    def insert_if_new(self, values: dict[str, Any]) -> bool:
        """`INSERT … ON CONFLICT (temporal_workflow_id, activity_id, user_id) DO NOTHING`.

        True when this call wrote the row; False when the same activity already notified that user
        (a retry). The tenant always comes from the context, never from `values`.
        """
        dialect = postgresql if self.session.get_bind().dialect.name == "postgresql" else sqlite
        stmt = (
            dialect.insert(Notification)
            .values(**{**values, "tenant_id": self.ctx.tenant_id, "row_version": 1})
            .on_conflict_do_nothing(
                index_elements=["temporal_workflow_id", "activity_id", "user_id"]
            )
            .returning(Notification.id)  # `rowcount` is -1 for this insert on psycopg
        )
        return self.session.execute(stmt).first() is not None

    def _mine(self, user_id: uuid.UUID) -> Any:
        return self._scoped().where(Notification.user_id == user_id)

    def get_for_user(self, user_id: uuid.UUID, id: uuid.UUID) -> Notification | None:
        return self.session.scalars(self._mine(user_id).where(Notification.id == id)).one_or_none()

    def page(
        self,
        user_id: uuid.UUID,
        *,
        unread_only: bool,
        limit: int,
        after: uuid.UUID | None = None,
    ) -> list[Notification]:
        """Newest first (UUIDv7 ids sort by creation). To know whether another page exists, ask
        for `limit + 1` rows."""
        query = self._mine(user_id)
        if unread_only:
            query = query.where(Notification.read_at.is_(None))
        if after is not None:
            query = query.where(Notification.id < after)
        rows = self.session.scalars(
            query.order_by(Notification.id.desc()).limit(min(limit, PAGE_LIMIT_MAX + 1))
        )
        return list(rows)

    def unread_count(self, user_id: uuid.UUID) -> int:
        query = select(func.count()).select_from(
            self._mine(user_id).where(Notification.read_at.is_(None)).subquery()
        )
        return int(self.session.scalar(query) or 0)

    def mark_read(self, user_id: uuid.UUID, id: uuid.UUID | None = None) -> int:
        """Set `read_at` on unread rows only (one notification, or all of the user's). Rows that
        are already read are untouched, so `read_at` is set once. Returns how many changed."""
        now = utcnow()
        stmt = (
            update(Notification)
            .where(
                Notification.tenant_id == self.ctx.tenant_id,
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
                Notification.deleted_at.is_(None),
            )
            .values(read_at=now, updated_at=now, row_version=Notification.row_version + 1)
            .execution_options(synchronize_session=False)
        )
        if id is not None:
            stmt = stmt.where(Notification.id == id)
        return int(self.session.execute(stmt).rowcount)  # type: ignore[attr-defined]
