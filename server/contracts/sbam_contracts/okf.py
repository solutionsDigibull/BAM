"""OKF bundle contracts (E5.3, TD §5.3, ADR 0001 §5): the six parts of a knowledge version's bundle.

    bundle.json       BundleManifest: sources, master snapshot, parts {name: storage key + sha256}
    entities.jsonl    one OkfEntity per line, each with >= 1 source ref
    relations.jsonl   one OkfRelation per line; `from` / `to` are entity ids of this bundle
    chunks.jsonl      one OkfChunk per line; a document's chunks joined = its doc.md, byte for byte
    masters.jsonl     one MasterNode per line (E3.6)
    coverage.json     BundleCoverage: per document COVERED | CHUNKS_ONLY | FAILED

Every part is canonical JSON (RFC 8785), so the same input gives the same bytes and hashes; the
creation time is kept on the knowledge version, outside the hashed manifest (ADR 0001 §5).
`okf_sha256` is the sha256 of bundle.json's bytes.
"""

from enum import StrEnum
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema

OKF_VERSION: Final = "1.0"
SHA256_HEX: Final = r"^[0-9a-f]{64}$"
BUNDLE: Final = "bundle.json"
PARTS: Final = (
    "entities.jsonl",
    "relations.jsonl",
    "chunks.jsonl",
    "masters.jsonl",
    "coverage.json",
)
Sha256 = Annotated[str, Field(pattern=SHA256_HEX)]


class _Part(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Location(_Part):
    """Where a chunk is in its source (E4.5's doc.json provenance). `scope` is `document` when only
    the whole document is known (no doc.json, or an entity found in no block)."""

    scope: Literal["block", "document"] = "block"
    page: int | None = None
    sheet: str | None = None
    cell: str | None = None
    line: int | None = None


class SourceRef(_Part):
    document_version_id: UUID
    chunk_id: str
    location: Location


class OkfChunk(_Part):
    id: str  # "<document_version_id>#c<n>"
    document_version_id: UUID
    location: Location
    text: str
    ocr: str | None = None  # "llm" for an OCR'd page (E4.6)
    unreadable: bool = False


class OkfEntity(_Part):
    id: str
    type: str
    name: str
    attributes: dict[str, str] = Field(default_factory=dict)
    source_refs: Annotated[list[SourceRef], Field(min_length=1)]


class OkfRelation(_Part):
    id: str
    type: str
    from_: str = Field(alias="from")
    to: str
    attributes: dict[str, str] = Field(default_factory=dict)
    source_refs: Annotated[list[SourceRef], Field(min_length=1)]


class CoverageStatus(StrEnum):
    COVERED = "COVERED"  # chunks and >= 1 entity
    CHUNKS_ONLY = "CHUNKS_ONLY"  # chunks, no entity: still covered (full text is in the bundle)
    FAILED = "FAILED"  # not in the graph; `reason` says why


class DocumentCoverage(_Part):
    document_version_id: UUID
    status: CoverageStatus
    chunks: Annotated[int, Field(ge=0)] = 0
    entities: Annotated[int, Field(ge=0)] = 0
    reason: str | None = None


class BundleCoverage(_Part):
    documents: list[DocumentCoverage]
    dropped_entities: Annotated[int, Field(ge=0)] = 0  # graph nodes without any source ref
    warnings: list[str] = Field(default_factory=list)  # codes and ids only, never text


class BundleSource(_Part):
    document_version_id: UUID
    sha256: Sha256  # of the doc.md the chunks were cut from
    type: Literal["md"] = "md"


class PartRef(_Part):
    storage_key: Annotated[str, Field(min_length=1, max_length=255)]
    sha256: Sha256


class MasterSnapshotRef(_Part):
    snapshot_id: UUID
    sha256: Sha256  # of masters.jsonl


class BundleManifest(_Part):
    id: UUID  # the knowledge version id
    project_id: UUID
    workflow_id: UUID | None = None  # null before a SOW is approved
    knowledge_version: Annotated[int, Field(ge=1)]
    okf_version: Literal["1.0"] = OKF_VERSION
    sources: list[BundleSource]
    master_snapshot_ref: MasterSnapshotRef | None = None
    parts: dict[str, PartRef]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/okf/`."""
    return {
        "bundle": contract_schema(BundleManifest, "okf/bundle"),
        "entity": contract_schema(OkfEntity, "okf/entity"),
        "relation": contract_schema(OkfRelation, "okf/relation"),
        "chunk": contract_schema(OkfChunk, "okf/chunk"),
        "coverage": contract_schema(BundleCoverage, "okf/coverage"),
    }
