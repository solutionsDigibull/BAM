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
  * Members (E2.4): PUT replaces the whole set. Every id must be an ACTIVE `sbam-fde` user of the
    tenant (422 on `user_ids.<i>` otherwise); at most 50, no duplicates. An archived project
    answers 409 PROJECT_ARCHIVED.
  * Workspace (E4.1): `GET /projects/{id}/pipeline` returns `state` (always equal to
    `pipeline_state`), the `steps` in pipeline order and the document `counts`. A step is
    `pending` until the pipeline reaches it; `started_at` / `ended_at` come from the audit trail.
    Counts ignore deleted and purged versions: `uploaded` = received and not yet settled (uploaded,
    scanning, parsing), `parsed`, `rejected` (malware) and `failed` (scan or parse failed).
    `GET /projects/{id}/workflows` is read-only: Workflows are created by SBAM, never by a client.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.identity import TenantUser
from sbam_contracts.state_machines import ProjectPipelineState, WorkflowState

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
    # E3.6: let Restricted master entries into this project's knowledge context (default false).
    allow_restricted: bool | None = None

    @field_validator("name")
    @classmethod
    def _name_not_null(cls, value: str | None) -> str:
        if value is None:  # runs only when sent: omitting `name` keeps it
            raise ValueError("name cannot be null")
        return value

    @field_validator("allow_restricted")
    @classmethod
    def _flag_not_null(cls, value: bool | None) -> bool:
        if value is None:
            raise ValueError("allow_restricted cannot be null")
        return value


class Project(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    client_name: str | None
    description: str | None
    context_notes: str | None
    allow_restricted: bool = False
    status: ProjectStatus
    pipeline_state: ProjectPipelineState
    row_version: int
    created_at: datetime
    updated_at: datetime


class ProjectMembersPut(_Request):
    user_ids: list[UUID] = Field(max_length=50)

    @field_validator("user_ids")
    @classmethod
    def _unique(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("user_ids must be unique")
        return value


class ProjectMembers(BaseModel):
    project_id: UUID
    members: list[TenantUser]


class PipelineStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineStep(BaseModel):
    name: ProjectPipelineState
    status: PipelineStepStatus
    started_at: datetime | None
    ended_at: datetime | None


class PipelineCounts(BaseModel):
    uploaded: int = Field(ge=0)
    parsed: int = Field(ge=0)
    rejected: int = Field(ge=0)
    failed: int = Field(ge=0)


class PipelineTimeline(BaseModel):
    """`GET /projects/{id}/pipeline`: the dashboard's pipeline panel."""

    state: ProjectPipelineState
    steps: list[PipelineStep]
    counts: PipelineCounts


# "<project name> v<MAJOR>" by default (E8.1): room for the suffix after a 120-character name.
WorkflowName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160, pattern=_NO_CONTROL)
]
WORKFLOW_VERSION = r"^[1-9][0-9]*\.0\.[0-9]+$"  # MAJOR.0.PATCH (TD §7, E8.1)


class WorkflowSummary(BaseModel):
    """One Workflow SBAM created for an approved SOW version (read-only but for its name)."""

    id: UUID
    project_id: UUID
    sow_version_id: UUID
    name: str
    version: Annotated[str, Field(pattern=WORKFLOW_VERSION)]
    state: WorkflowState
    created_by: str  # always SYSTEM: SBAM creates Workflows, nobody else (E8.1)
    created_at: datetime
    row_version: int


class WorkflowRename(BaseModel):
    """PATCH W (E8.1): the name is the only thing a person may change on a Workflow."""

    model_config = ConfigDict(extra="forbid")

    name: WorkflowName


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/projects/`."""
    models: dict[str, type[BaseModel]] = {
        "project": Project,
        "project_create": ProjectCreate,
        "project_patch": ProjectPatch,
        "project_members_put": ProjectMembersPut,
        "project_members": ProjectMembers,
        "pipeline_timeline": PipelineTimeline,
        "workflow_summary": WorkflowSummary,
        "workflow_rename": WorkflowRename,
    }
    return {stem: contract_schema(model, f"projects/{stem}") for stem, model in models.items()}
