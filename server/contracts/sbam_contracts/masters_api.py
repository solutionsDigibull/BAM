"""Masters engine API contracts: entries, immutable versions and project snapshots (TD §7.3, E3.2).

Rules worth knowing
  * The master type is in the path (`/admin/masters/{type}`) and selects the payload model
    (`masters_workbook.PAYLOAD_MODELS`); the payload is validated against it with
    `additionalProperties: false`, so errors point at `payload.<field>`.
  * Admins create ORGANIZATION, RULES, AGENTS, SKILLS and TOOLING entries. FILETYPE (and README)
    change only through the super-admin workbook import (E3.3).
  * `stable_id`, `status` and `source_kind` are derived from the row (`masters.identify`) and kept
    beside the payload, never inside it.
  * A version is never edited: a new payload is a new version n+1. The POST takes the entry's ETag
    as `If-Match`; a version that lost a race answers 409 VERSION_CONFLICT.
  * `row_label` is optional on create: the API assigns the next `<prefix> #n` (`Rule #7`). Tooling
    rows are labelled with their stable id, so a TOOLING create must send it (`TOOL-MFG-12`).
  * A snapshot pins one version per entry for a project: project-bound entries override tenant
    entries with the same stable id; ILLUSTRATIVE, RETIRED and Restricted entries are left out.
    `sha256` is `canonical.sha256_of({project_id, entries})` (RFC 8785, `sha256:<hex>`), so the same
    versions always give the same hash.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.masters import MasterStatus, MasterType, SourceKind

RowLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^\S.*$")
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MasterEntryCreate(_Request):
    payload: dict[str, Any]
    row_label: RowLabel | None = None
    project_id: UUID | None = None  # bind the entry to one project of the caller's tenant


class MasterVersionCreate(_Request):
    """The full new payload. The entry's ETag goes in `If-Match`."""

    payload: dict[str, Any]


class MasterStatusChange(_Request):
    """PATCH .../status (E3.5): the status is written back into the row (Misc, or Tooling's own
    Status column) as a new version. The entry's ETag goes in `If-Match`."""

    status: MasterStatus


class MasterEntry(BaseModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID | None
    master_type: MasterType
    stable_id: str
    row_label: str
    status: MasterStatus
    source_kind: SourceKind
    version: int = Field(ge=1)
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SnapshotEntry(BaseModel):
    entry_id: UUID
    master_type: MasterType
    stable_id: str
    version: int = Field(ge=1)


class MastersSnapshot(BaseModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID
    entries: list[SnapshotEntry]
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/masters/`."""
    models: dict[str, type[BaseModel]] = {
        "master_entry_create": MasterEntryCreate,
        "master_version_create": MasterVersionCreate,
        "master_status_change": MasterStatusChange,
        "master_entry": MasterEntry,
        "masters_snapshot": MastersSnapshot,
    }
    return {stem: contract_schema(model, f"masters/{stem}") for stem, model in models.items()}


def snapshot_document(project_id: UUID, entries: list[SnapshotEntry]) -> dict[str, Any]:
    """What `MastersSnapshot.sha256` hashes: the project and its pinned entries, sorted."""
    ordered = sorted(entries, key=lambda e: (e.master_type.value, e.stable_id, str(e.entry_id)))
    return {
        "project_id": str(project_id),
        "entries": [e.model_dump(mode="json") for e in ordered],
    }
