"""Knowledge (Cognee) adapter contracts (E5.1, TD §5.1, ADR 0001).

Every call to the Cognee adapter takes a `ProjectScope` first: the tenant and project the data
belongs to. The dataset name is derived from it server-side, never from user input. Items and
results carry ids only, never document text (TD §13.1).
"""

from enum import StrEnum
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts.okf import BundleCoverage

SHA256_HEX: Final = r"^[0-9a-f]{64}$"
DATASET_PATTERN: Final = r"^sbam_[0-9a-f-]{36}_[0-9a-f-]{36}$"
MAX_TOP_K: Final = 50
MAX_GRAPH_PAGE: Final = 500


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


KNOWLEDGE_REBUILD: Final = "KnowledgeRebuild"  # workflow type of POST P/knowledge/rebuild (E5.2)


def knowledge_workflow_id(project_id: UUID) -> str:
    """One rebuild at a time per project (Temporal's FAIL conflict policy)."""
    return f"knowledge-{project_id}"


def cognee_dataset(tenant_id: UUID, project_id: UUID) -> str:
    """The project's Cognee dataset, `sbam_<tenant>_<project>` (E5.1); the adapter asserts it."""
    return f"sbam_{tenant_id}_{project_id}"


class ProjectScope(_Dto):
    """Tenant + project of every Cognee call (E5.1). SBAM ids are UUIDv7, so any UUID version."""

    tenant_id: UUID
    project_id: UUID


class ItemSource(StrEnum):
    DOCUMENT = "DOCUMENT"  # a parsed document version (doc.md / doc.json)
    MASTERS = "MASTERS"  # the project's master context, masters.jsonl (E3.6)


class KnowledgeItem(_Dto):
    """One file to add to the project's dataset. `source_id` is the document version id, or the
    master snapshot id for MASTERS. `source_id` + `sha256` is the idempotency key (E5.1-T08)."""

    source: ItemSource
    source_id: UUID
    sha256: Annotated[str, Field(pattern=SHA256_HEX)]
    storage_key: Annotated[str, Field(min_length=1, max_length=255)]


class AddResult(_Dto):
    added: list[str] = Field(default_factory=list)  # item keys `<source_id>_<sha256>`
    skipped: list[str] = Field(default_factory=list)  # already in the dataset (a retry)


class SearchHit(_Dto):
    """One search result. `text` is shown to the caller only; it is never logged."""

    id: str
    text: str
    score: float | None = None
    source_id: str | None = None  # the item key the hit came from, when Cognee reports it


class GraphNode(_Dto):
    id: str
    type: str | None = None
    name: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(_Dto):
    source: str
    target: str
    label: str


class GraphPage(_Dto):
    """Nodes sorted by id; the edges whose source node is on this page, so every edge appears on
    exactly one page. `next_cursor` is None on the last page. Totals cover the whole dataset
    (E5.1-T06 compares them across a restart)."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    next_cursor: str | None = None
    total_nodes: int
    total_edges: int


# --- knowledge versions (E5.2) -------------------------------------------------------------------


class SourceStatus(StrEnum):
    """A document's fate in one knowledge build (E5.2); the OKF bundle (E5.3) refines ADDED."""

    ADDED = "ADDED"  # in the Cognee dataset of this version
    FAILED = "FAILED"  # not parsed, rejected or Cognee refused it; `reason` says which


class CoverageEntry(_Dto):
    document_version_id: UUID
    status: SourceStatus
    reason: str | None = None  # an error code or type, never document text


class KnowledgeCoverage(_Dto):
    documents: list[CoverageEntry] = Field(default_factory=list)
    masters_included: int = 0  # master entries in masters.jsonl (E3.6)
    entities: int = 0  # graph nodes after cognify
    bundle: BundleCoverage | None = None  # the OKF bundle's view (E5.3), once it is built


class KnowledgeVersionOut(_Dto):
    """`GET P/knowledge` and the rebuild response's subject (E5.2, TD §7)."""

    id: UUID
    project_id: UUID
    version: Annotated[int, Field(ge=1)]
    status: str  # KnowledgeVersionState
    cognee_dataset: str
    master_snapshot_id: UUID | None = None
    okf_bundle_key: str | None = None
    okf_sha256: str | None = None
    coverage: KnowledgeCoverage = Field(default_factory=KnowledgeCoverage)
    error_reason: str | None = None
    run_id: UUID | None = None
