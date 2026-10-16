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
from typing import Annotated, Any, Literal
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


class FiletypeInfo(BaseModel):
    """GET /admin/filetypes (E3.7): a frozen Filetype row plus SBAM's loader configuration."""

    row_label: str
    file_format: str
    tier: int = Field(ge=1, le=3)
    purpose: str
    rag_ingestion: str
    example: str
    mvp_enabled: bool
    loader: str | None
    extensions: list[str]
    intent_class: str
    default_route: str


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
    """One pinned entry. The snapshot pins every live entry of the project; whoever uses it decides
    inclusion from `status` and `restricted` (E3.6: ILLUSTRATIVE / RETIRED never, Restricted only
    when the project allows it)."""

    entry_id: UUID
    master_type: MasterType
    stable_id: str
    version: int = Field(ge=1)
    status: MasterStatus
    restricted: bool = False


ExclusionReason = Literal["RESTRICTED", "ILLUSTRATIVE", "RETIRED"]


class MasterNode(BaseModel):
    """One line of masters.jsonl (E3.6): a master entry as a Cognee graph node.

    `node_id` = the RAG Reference ID Field, else the stable id; `node_type` = Entity / Node Type,
    else the master type. `parent_id` is the Parent Entity's node (None for roots: Organization
    rows, and entries whose parent is unknown). `Links / Path` stays inside `payload` as text."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    node_type: str
    master_type: MasterType
    stable_id: str
    entry_id: UUID
    version: int
    parent_id: str | None
    payload: dict[str, Any]


class ExcludedEntry(BaseModel):
    stable_id: str
    master_type: MasterType
    reason: ExclusionReason


class InclusionReport(BaseModel):
    """What went into masters.jsonl and what didn't; included + excluded = snapshot size."""

    snapshot_id: UUID
    dataset: str  # the Cognee dataset name: the project id
    storage_key: str  # where masters.jsonl was written (content-addressed)
    sha256: str  # of masters.jsonl
    included: list[str]  # node ids
    excluded: list[ExcludedEntry]
    refresh_due: list[str]  # stable ids whose Refresh Frequency has elapsed
    warnings: list[str]


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
        "master_entry": MasterEntry,
        "filetype_info": FiletypeInfo,
        "masters_snapshot": MastersSnapshot,
        "inclusion_report": InclusionReport,
    }
    return {stem: contract_schema(model, f"masters/{stem}") for stem, model in models.items()}


def snapshot_document(project_id: UUID, entries: list[SnapshotEntry]) -> dict[str, Any]:
    """What `MastersSnapshot.sha256` hashes: the project and its pinned entries, sorted."""
    ordered = sorted(entries, key=lambda e: (e.master_type.value, e.stable_id, str(e.entry_id)))
    return {
        "project_id": str(project_id),
        "entries": [e.model_dump(mode="json") for e in ordered],
    }
