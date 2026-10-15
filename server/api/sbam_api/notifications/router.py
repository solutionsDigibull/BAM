"""`/notifications` (E10.4, TD §8.13): the signed-in user's own in-app notifications.

Every query is scoped to the caller's tenant and user, so another user's notification looks
missing (404). Marking read is idempotent: `read_at` is set once, then left alone.
"""

import base64
import binascii
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sbam_contracts.api import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX, ErrorCode, FieldError, Page
from sbam_contracts.notifications import Notification as NotificationOut
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser, authenticated
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.session import app_sessions
from sbam_api.notifications.models import Notification
from sbam_api.notifications.repository import NotificationRepository

router = APIRouter(tags=["notifications"])

Me = Annotated[CurrentUser, Depends(authenticated)]


def db(request: Request) -> Iterator[Session]:
    with app_sessions(request.app.state)() as session:
        yield session


Db = Annotated[Session, Depends(db)]


def _repo(session: Session, user: CurrentUser) -> NotificationRepository:
    return NotificationRepository(session, user.context())  # no tenant -> 404


def _out(row: Notification) -> NotificationOut:
    return NotificationOut.model_validate(
        {name: getattr(row, name) for name in NotificationOut.model_fields}
    )


def _encode(row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(row_id.bytes).rstrip(b"=").decode()


def _decode(cursor: str) -> uuid.UUID:
    # Unsigned on purpose: the query is already limited to the caller's own rows, so a made-up
    # position can only move the caller around their own list.
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        raise ApiError(
            400,
            ErrorCode.INVALID_CURSOR,
            "Invalid request",
            errors=[FieldError(path="cursor", message="invalid or expired cursor")],
        ) from None


@router.get("/notifications")
def list_notifications(
    user: Me,
    session: Db,
    unread_only: bool = False,
    cursor: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=PAGE_LIMIT_MAX)] = PAGE_LIMIT_DEFAULT,
) -> Page[NotificationOut]:
    """Newest first."""
    after = _decode(cursor) if cursor else None
    rows = _repo(session, user).page(
        user.user_id, unread_only=unread_only, limit=limit + 1, after=after
    )
    more = len(rows) > limit
    page = rows[:limit]
    return Page[NotificationOut](
        items=[_out(r) for r in page], next_cursor=_encode(page[-1].id) if more else None
    )


@router.post("/notifications/read-all", status_code=204)
def read_all(user: Me, session: Db) -> Response:
    _repo(session, user).mark_read(user.user_id)
    session.commit()
    return Response(status_code=204)


@router.post("/notifications/{notification_id}/read", status_code=204)
def read_one(notification_id: uuid.UUID, user: Me, session: Db) -> Response:
    repo = _repo(session, user)
    if repo.get_for_user(user.user_id, notification_id) is None:
        raise not_found()  # missing, someone else's, or another tenant's: all the same
    repo.mark_read(user.user_id, notification_id)  # a no-op if already read
    session.commit()
    return Response(status_code=204)
