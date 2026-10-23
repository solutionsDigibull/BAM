"""The fixed event names and how an outbox row becomes a PostHog event (E16.2).

Only whitelisted properties leave the platform: ids, the stage, a duration, the environment and, for
a failed gate, the gate's name. Never a payload passthrough, so no document text, SOW body, names,
emails or secrets can reach PostHog (E16.2-T05). The person is a salted hash of their user id.
"""

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID


class AnalyticsEvent(StrEnum):
    WORKFLOW_CREATED = "workflow_created"
    SOW_APPROVED = "sow_approved"
    GENERATE_STARTED = "generate_started"
    GATE_FAILED = "gate_failed"
    CORRECTION_ATTEMPTED = "correction_attempted"
    SUB_SKILL_CERTIFIED = "sub_skill_certified"
    WORKFLOW_FROZEN = "workflow_frozen"
    SOW_EDITED = "sow_edited"
    ISATVON_EDITED = "isatvon_edited"


# outbox `type` ("<machine>.<event>", state_machine._finish) -> product event; anything else is
# not a product event and is skipped.
EVENTS: Final[dict[str, AnalyticsEvent]] = {
    "project_pipeline.workflow_created": AnalyticsEvent.WORKFLOW_CREATED,
    "sow_version.approve": AnalyticsEvent.SOW_APPROVED,
    "sow_version.new_version": AnalyticsEvent.SOW_EDITED,  # a save creates a version (E6.5)
    "sow_version.edit": AnalyticsEvent.SOW_EDITED,
    "workflow.generate": AnalyticsEvent.GENERATE_STARTED,
    "workflow.freeze": AnalyticsEvent.WORKFLOW_FROZEN,
    "sub_skill.gate_failed": AnalyticsEvent.GATE_FAILED,
    "sub_skill.correction_applied": AnalyticsEvent.CORRECTION_ATTEMPTED,
    "sub_skill.tests_passed": AnalyticsEvent.SUB_SKILL_CERTIFIED,
    "sub_skill.edit": AnalyticsEvent.ISATVON_EDITED,  # an ISATVON edit sends it back to DRAFT
}

# machines whose entity is (or belongs to) a Workflow: their entity id goes out as workflow_id
_WORKFLOW_MACHINES: Final = frozenset({"workflow"})
_DETAIL_KEYS: Final = ("gate",)  # the only transition details that may leave, see TransitionCommand


def event_for(outbox_type: str) -> AnalyticsEvent | None:
    return EVENTS.get(outbox_type)


def distinct_id(actor_id: str, salt: str) -> str:
    """A stable, salted hash: the same person every time, never their user id."""
    return hashlib.sha256(f"{salt}:{actor_id}".encode()).hexdigest()


def build_event(
    *,
    row_id: UUID,
    outbox_type: str,
    tenant_id: UUID,
    payload: dict[str, Any],
    actor_id: str,
    at: datetime,
    previous_at: datetime | None,
    environment: str,
    salt: str,
) -> dict[str, Any] | None:
    """One PostHog batch item for an outbox row, or None when the row is no product event."""
    event = event_for(outbox_type)
    if event is None:
        return None
    machine = outbox_type.split(".", 1)[0]
    raw = payload.get("details")
    details: dict[str, Any] = raw if isinstance(raw, dict) else {}
    properties: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "project_id": payload.get("project_id"),
        "workflow_id": payload.get("entity_id") if machine in _WORKFLOW_MACHINES else None,
        "subject_id": payload.get("entity_id"),
        "stage": payload.get("to_state"),
        "duration_ms": (
            int((at - previous_at).total_seconds() * 1000) if previous_at is not None else None
        ),
        "environment": environment,
    }
    if machine == "sub_skill":
        properties["sub_skill_id"] = payload.get("entity_id")
    properties.update({k: str(details[k]) for k in _DETAIL_KEYS if k in details})
    return {
        "event": event.value,
        "distinct_id": distinct_id(actor_id, salt),
        "uuid": str(row_id),  # PostHog drops a second event with the same uuid (E16.2-T07)
        "timestamp": at.isoformat(),
        "properties": properties,
    }
