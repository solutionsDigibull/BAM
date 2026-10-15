"""`notifications`: in-app notifications, one row per recipient (TD §7.8, §8.13, issue E10.4).

`(temporal_workflow_id, activity_id, user_id)` is unique, so an activity that retries after its
insert committed (or an outbox row sent twice) never writes a second row for the same user.
"""

import uuid
from datetime import datetime

from sbam_contracts.notifications import BODY_MAX, TITLE_MAX, NotificationType
from sqlalchemy import DateTime, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, TenantOwned, status_check

LINK_MAX = 300


class Notification(TenantOwned, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("temporal_workflow_id", "activity_id", "user_id"),
        status_check(NotificationType, column="type"),
        Index("ix_notifications_user_id_id", "user_id", "id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # no FK: workflows arrive with E8
    type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(TITLE_MAX))
    body: Mapped[str] = mapped_column(String(BODY_MAX))
    link: Mapped[str | None] = mapped_column(String(LINK_MAX))  # relative, /projects/... only
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What produced it: the Temporal workflow and activity ids, or ("outbox", outbox row id).
    temporal_workflow_id: Mapped[str] = mapped_column(String(255))
    activity_id: Mapped[str] = mapped_column(String(255))
