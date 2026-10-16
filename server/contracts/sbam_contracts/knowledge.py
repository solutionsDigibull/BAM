"""Knowledge (Cognee) adapter contracts (E5.1, TD §5.1, ADR 0001).

Every call to the Cognee adapter takes a `ProjectScope` first: the tenant and project the data
belongs to. The dataset name is derived from it server-side, never from user input. Items and
results carry ids only, never document text (TD §13.1).
"""

from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import UUID4, BaseModel, ConfigDict, Field

SHA256_HEX: Final = r"^[0-9a-f]{64}$"
DATASET_PATTERN: Final = r"^sbam_[0-9a-f-]{36}_[0-9a-f-]{36}$"
MAX_TOP_K: Final = 50
MAX_GRAPH_PAGE: Final = 500


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectScope(_Dto):
    """Tenant + project of every Cognee call; both UUIDv4 (E5.1)."""

    tenant_id: UUID4
    project_id: UUID4


class ItemSource(StrEnum):
    DOCUMENT = "DOCUMENT"  # a parsed document version (doc.md / doc.json)
    MASTERS = "MASTERS"  # the project's master context, masters.jsonl (E3.6)


class KnowledgeItem(_Dto):
    """One file to add to the project's dataset. `source_id` is the document version id, or the
    master snapshot id for MASTERS. `source_id` + `sha256` is the idempotency key (E5.1-T08)."""

    source: ItemSource
    source_id: UUID4
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
