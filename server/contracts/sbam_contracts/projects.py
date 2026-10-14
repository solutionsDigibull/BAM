"""Admin project contracts (TD §8.3, issue E3.1).

Rules worth knowing
  * A project name is trimmed, then must be 1..120 characters with no control characters. Names
    are unique per tenant ignoring case (409 DUPLICATE_NAME). Two tenants may share a name.
  * The tenant always comes from the caller's token, never from the body.
  * A project is ACTIVE or ARCHIVED. An ARCHIVED project is read-only: edits answer 409
    PROJECT_ARCHIVED.
  * Archive answers 409 PIPELINE_RUNNING unless `pipeline_state` is idle, failed, in SOW review,
    has a Workflow created, or is cancelled.
  * Edits need `If-Match` (428 when missing, 412 when stale).
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.state_machines import ProjectPipelineState

_NO_CONTROL = r"^[^\x00-\x1f\x7f-\x9f]*$"

ProjectName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=_NO_CONTROL)
]
ClientName = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=120, pattern=_NO_CONTROL)
]
Description = Annotated[str, StringConstraints(max_length=2000)]
ContextNotes = Annotated[str, StringConstraints(max_length=10000)]  # Markdown, stored as text


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(_Request):
    name: ProjectName
    client_name: ClientName | None = None
    description: Description | None = None
    context_notes: ContextNotes | None = None


class ProjectPatch(_Request):
    """Partial update: only the fields sent change. `null` clears an optional field."""

    name: ProjectName | None = None
    client_name: ClientName | None = None
    description: Description | None = None
    context_notes: ContextNotes | None = None

    @field_validator("name")
    @classmethod
    def _name_not_null(cls, value: str | None) -> str:
        if value is None:  # runs only when sent: omitting `name` keeps it
            raise ValueError("name cannot be null")
        return value


class Project(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    client_name: str | None
    description: str | None
    context_notes: str | None
    status: ProjectStatus
    pipeline_state: ProjectPipelineState
    row_version: int
    created_at: datetime
    updated_at: datetime


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/projects/`."""
    models: dict[str, type[BaseModel]] = {
        "project": Project,
        "project_create": ProjectCreate,
        "project_patch": ProjectPatch,
    }
    return {stem: contract_schema(model, f"projects/{stem}") for stem, model in models.items()}
