"""The change notes of a Workflow, kept in the audit trail (#767, WG-19).

Two kinds of audit row on the `workflow` entity, both tenant-scoped like every audit read:

* `REQUEST_CHANGE_NOTE`: the FDE's note (`note_id`, `sub_skill_id`, `workflow_version`, `note`).
  It counts against the Workflow version it was written for.
* `REQUEST_CHANGE_OUTCOME`: what became of a note (`note_id`, `status`, `reason`). The newest
  outcome of a note is its status; a note with none is still PROCESSING. A note has one outcome:
  the key makes a repeat (a retried activity) find the first.

Nothing here reads a note for any purpose but showing it, counting it, or handing its text to the
coder as data (`coder_notes`). No gate reads notes.
"""

import uuid
from typing import Final

from sbam_contracts.request_change import MAX_NOTES_PER_VERSION, ChangeNote, ChangeNoteStatus
from sqlalchemy.orm import Session

from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import entity_history, find_by_key, record

NOTE: Final = "REQUEST_CHANGE_NOTE"
OUTCOME: Final = "REQUEST_CHANGE_OUTCOME"
ENTITY: Final = "workflow"
NOTE_KEY: Final = "request-change:"
OUTCOME_KEY: Final = "request-change-outcome:"
WORKER: Final = "sbam-request-change"
# The notes whose text the coder gets as data: the code is to change and the Sub-Skill's meaning
# does not (a HELD_PATCH note is carried by the accepted ISATVON, a PARKED one never ran).
CODER_STATUSES: Final = frozenset({ChangeNoteStatus.REBUILDING, ChangeNoteStatus.QUEUED})


def find_note(
    session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID, key: str
) -> AuditLog | None:
    return find_by_key(session, tenant_id, ENTITY, str(workflow_id), NOTE_KEY + key)


def record_note(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: str,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID,
    version: str,
    text: str,
    key: str,
    correlation_id: str,
) -> uuid.UUID:
    note_id = uuid.uuid4()
    record(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=NOTE,
        entity_type=ENTITY,
        entity_id=str(workflow_id),
        correlation_id=correlation_id,
        after={
            "note_id": str(note_id),
            "sub_skill_id": str(sub_skill_id),
            "workflow_version": version,
            "note": text,
        },
        idempotency_key=NOTE_KEY + key,
    )
    return note_id


def record_outcome(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    note_id: uuid.UUID,
    status: ChangeNoteStatus,
    reason: str | None,
    correlation_id: str,
    actor_id: str = WORKER,
) -> ChangeNoteStatus:
    """Writes the outcome once; returns the status that is recorded (the first one wins)."""
    key = OUTCOME_KEY + str(note_id)
    existing = find_by_key(session, tenant_id, ENTITY, str(workflow_id), key)
    if existing is not None:
        return ChangeNoteStatus((existing.after or {})["status"])
    record(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=OUTCOME,
        entity_type=ENTITY,
        entity_id=str(workflow_id),
        correlation_id=correlation_id,
        after={"note_id": str(note_id), "status": status.value, "reason": (reason or "")[:500]},
        idempotency_key=key,
    )
    return status


def notes_of(
    session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID, version: str
) -> list[ChangeNote]:
    """The notes written for this Workflow version, oldest first, with their current status."""
    rows = entity_history(session, tenant_id, ENTITY, str(workflow_id))
    outcomes: dict[str, tuple[ChangeNoteStatus, str | None]] = {}
    for row in rows:
        if row.action == OUTCOME and row.after:
            outcomes[row.after["note_id"]] = (
                ChangeNoteStatus(row.after["status"]),
                row.after.get("reason") or None,
            )
    notes: list[ChangeNote] = []
    for row in rows:
        after = row.after or {}
        if row.action != NOTE or after.get("workflow_version") != version:
            continue
        status, reason = outcomes.get(after["note_id"], (ChangeNoteStatus.PROCESSING, None))
        notes.append(
            ChangeNote(
                id=uuid.UUID(after["note_id"]),
                sub_skill_id=uuid.UUID(after["sub_skill_id"]),
                workflow_version=version,
                note=str(after["note"]),
                status=status,
                reason=reason,
                created_at=row.at,
            )
        )
    return notes


def count(session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID, version: str) -> int:
    return len(notes_of(session, tenant_id, workflow_id, version))


def get_note(
    session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID, version: str, note_id: uuid.UUID
) -> ChangeNote | None:
    return next(
        (n for n in notes_of(session, tenant_id, workflow_id, version) if n.id == note_id), None
    )


def coder_notes(
    session: Session,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    version: str,
    sub_skill_id: uuid.UUID,
) -> list[str]:
    """The text of the notes about this Sub-Skill the coder gets, as data (at most the limit)."""
    return [
        n.note
        for n in notes_of(session, tenant_id, workflow_id, version)
        if n.sub_skill_id == sub_skill_id and n.status in CODER_STATUSES
    ][:MAX_NOTES_PER_VERSION]
