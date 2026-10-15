"""E10.4 notification contract: the type enum, the length limits and the committed schema."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.notifications import Notification, NotificationType


def make(**over: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "workflow_id": None,
        "type": "SOW_READY",
        "title": "t",
        "body": "b",
        "link": None,
        "created_at": datetime.now(UTC),
        "read_at": None,
        **over,
    }


def test_e10_4_the_ten_notification_types() -> None:
    assert {t.value for t in NotificationType} == {
        "SOW_READY", "BREAKDOWN_READY", "SUBSKILL_NEEDS_REVIEW", "WORKFLOW_CERTIFIED",
        "WORKFLOW_FROZEN", "EXPORT_READY", "DOCUMENT_REJECTED", "GENERATION_FAILED",
        "STAGE_COMPLETED", "REMINDER",
    }  # fmt: skip


@pytest.mark.parametrize("field,limit", [("title", 120), ("body", 1000)])
def test_e10_4_title_and_body_limits(field: str, limit: int) -> None:
    assert Notification(**make(**{field: "x" * limit}))
    with pytest.raises(ValidationError):
        Notification(**make(**{field: "x" * (limit + 1)}))


def test_e10_4_unknown_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Notification(**make(type="EMAIL_SENT"))
