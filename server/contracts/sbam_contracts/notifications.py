"""In-app notification contracts (TD §8.13, §17, issue E10.4, decision C-43).

Rules worth knowing
  * Notifications are in-app only: there is no email, SMS or webhook sender.
  * `type` is one of `NotificationType`. `title` is at most 120 characters, `body` at most 1000.
  * `link` is a relative app path under `/projects/`, or null. Anything else (another scheme, a
    host, `//`, `..`, `javascript:`) is dropped by the service and the notification is stored
    without a link.
  * A notification belongs to one user. Another user's notification looks missing (404).
  * `read_at` is set once; marking it read again changes nothing (204 both times).
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel, Field

from sbam_contracts._jsonschema import contract_schema

TITLE_MAX: Final = 120
BODY_MAX: Final = 1000
LINK_PREFIX: Final = "/projects/"


class NotificationType(StrEnum):
    SOW_READY = "SOW_READY"
    BREAKDOWN_READY = "BREAKDOWN_READY"
    SUBSKILL_NEEDS_REVIEW = "SUBSKILL_NEEDS_REVIEW"
    WORKFLOW_CERTIFIED = "WORKFLOW_CERTIFIED"
    WORKFLOW_FROZEN = "WORKFLOW_FROZEN"
    EXPORT_READY = "EXPORT_READY"
    DOCUMENT_REJECTED = "DOCUMENT_REJECTED"
    GENERATION_FAILED = "GENERATION_FAILED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    REMINDER = "REMINDER"


class Notification(BaseModel):
    id: UUID
    project_id: UUID | None
    workflow_id: UUID | None
    type: NotificationType
    title: str = Field(max_length=TITLE_MAX)
    body: str = Field(max_length=BODY_MAX)
    link: str | None
    created_at: datetime
    read_at: datetime | None


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/notifications/`."""
    return {"notification": contract_schema(Notification, "notifications/notification")}
