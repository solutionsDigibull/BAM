"""Request change (#767, WG-19, ADR 0028 decision 8, ADR 0032 decision 5).

The generated code is read-only for the FDE. A problem found in the code or the demo (also from
Staging) goes through a plain-words note:

- `POST W/request-change` (`RequestChangeCreate` + `Idempotency-Key`) -> 202
  `RequestChangeAccepted`.
  The note is stored in the audit trail and counted against its workflow version: at most
  `MAX_NOTES_PER_VERSION` (409 after that, with the ISATVON Grid as the way to change it).
- `GET W/request-change` -> `ChangeNoteList`: the notes of the Workflow's current version and what
  became of each.

The note reaches the model only as a `<data>` block (`note` for the classifier, `change_notes` for
the coder), never as instructions, and no gate reads it. The classifier answers `ChangeNoteAnswer`:
`CODE` (the Sub-Skill means the same, the code is wrong: the coder writes a new generated version
with the note as data) or `MEANING` (what the Sub-Skill means changes: a proposed ISATVON patch the
FDE accepts or rejects through the held-patch flow, `held_patch.py`).
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.held_patch import MAX_HELD_OPS, HeldPatchOp
from sbam_contracts.runs import Run, RunInput

MAX_NOTES_PER_VERSION: Final = 3  # ADR 0028 decision 8
MAX_NOTE_CHARS: Final = 2_000
REQUEST_CHANGE_APPLY: Final = "RequestChangeApply"  # workflow type
Note = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NOTE_CHARS)
]


def request_change_workflow_id(workflow_id: UUID) -> str:
    """One note is applied at a time per Workflow (Temporal's FAIL conflict policy)."""
    return f"request-change-{workflow_id}"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChangeNoteStatus(StrEnum):
    PROCESSING = "PROCESSING"  # stored; the classifier has not answered yet
    REBUILDING = "REBUILDING"  # the code is wrong: the coder rebuilds with the note as data
    HELD_PATCH = "HELD_PATCH"  # the meaning changes: an ISATVON patch waits for the FDE
    PARKED = "PARKED"  # the token budget or the model stopped it; the FDE decides what next
    QUEUED = "QUEUED"  # no build is waiting (certified, Staging): kept for the next version


class ChangeNoteOutcome(StrEnum):
    CODE = "CODE"
    MEANING = "MEANING"


class RequestChangeCreate(_Strict):
    sub_skill_id: UUID
    note: Note


class ChangeNote(_Strict):
    id: UUID
    sub_skill_id: UUID
    workflow_version: Annotated[str, Field(max_length=20)]
    note: str
    status: ChangeNoteStatus
    reason: Annotated[str, Field(max_length=500)] | None = None  # why it was parked, or the model's
    created_at: datetime


class ChangeNoteList(_Strict):
    notes: list[ChangeNote] = Field(default_factory=list)
    used: Annotated[int, Field(ge=0)]
    limit: Annotated[int, Field(ge=1)] = MAX_NOTES_PER_VERSION


class RequestChangeAccepted(_Strict):
    note: ChangeNote
    run: Run | None = None  # null when no build is waiting (status QUEUED)


class ChangeNoteAnswer(_Strict):
    """What the classifier model answers. A MEANING answer carries the patch (RFC 6902, every
    path under `/isatvon_definition/`); a CODE answer carries none."""

    outcome: ChangeNoteOutcome
    rationale: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)
    ]
    isatvon_patch: list[HeldPatchOp] | None = Field(default=None, max_length=MAX_HELD_OPS)


class RequestChangeInput(RunInput):
    """`RequestChangeApply`'s input: ids only (the note's text is read from the audit trail)."""

    workflow_id: UUID
    sub_skill_id: UUID
    note_id: UUID
    build_workflow_id: Annotated[str, Field(min_length=1, max_length=200)]
    actor_id: Annotated[str, Field(min_length=1, max_length=255)]


class RequestChangeResult(_Strict):
    note_id: UUID
    status: ChangeNoteStatus


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/request_change/`."""
    models: dict[str, type[BaseModel]] = {
        "create": RequestChangeCreate,
        "accepted": RequestChangeAccepted,
        "note_list": ChangeNoteList,
        "answer": ChangeNoteAnswer,
    }
    return {
        stem: contract_schema(model, f"request_change/{stem}") for stem, model in models.items()
    }
