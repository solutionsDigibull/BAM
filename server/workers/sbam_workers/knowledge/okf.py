"""OKF bundle: build and validate (E5.3, TD §5.3, ADR 0001 §5). Pure functions, no I/O.

Inputs: the knowledge version's documents (doc.md text + doc.json blocks), Cognee's graph of the
project dataset, masters.jsonl and the per-document build outcome (E5.2). Output: the six parts as
canonical bytes. Design points (proved in the #26 spike):
  * chunks are cut from the normalized documents, not by Cognee's chunker, so a document's chunks
    joined are its doc.md byte for byte, whatever the graph extraction missed (C-13);
  * entities and relations come from the graph; each carries >= 1 source ref (document version +
    chunk + location). A node with none is dropped and counted; a node pointing at a document that
    is not in this version (another project's) is rejected: CROSS_PROJECT_REF, nothing written;
  * coverage: COVERED (chunks + entities), CHUNKS_ONLY (chunks, no entity), FAILED (not added).
`validate` re-checks the written bytes: hashes, references, full text, coverage.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sbam_contracts.canonical import canonical_json
from sbam_contracts.knowledge import CoverageEntry, GraphEdge, GraphNode, SourceStatus
from sbam_contracts.masters_api import MasterNode
from sbam_contracts.okf import (
    BUNDLE,
    PARTS,
    BundleCoverage,
    BundleManifest,
    BundleSource,
    CoverageStatus,
    DocumentCoverage,
    Location,
    MasterSnapshotRef,
    OkfChunk,
    OkfEntity,
    OkfRelation,
    PartRef,
    SourceRef,
)

STRUCTURAL_EDGES = frozenset(
    {"is_part_of", "contains", "made_from", "is_a", "exists_in", "has_summary"}
)
CROSS_PROJECT_REF = "CROSS_PROJECT_REF"
ZERO_COVERAGE = "ZERO_COVERAGE"
_LOCATION_KEYS = ("page", "sheet", "cell", "line")


class BundleInvalid(Exception):  # noqa: N818 - name from the E5.3 spec
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems[:20]))
        self.problems = problems


@dataclass(frozen=True)
class Doc:
    """One added document: its doc.md text, sha256 and doc.json blocks (may be empty)."""

    document_version_id: uuid.UUID
    text: str
    sha256: str
    blocks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Built:
    parts: dict[str, bytes]  # PARTS name -> canonical bytes
    coverage: BundleCoverage
    counts: dict[str, int]


def line(model: BaseModel) -> bytes:
    return canonical_json(model.model_dump(mode="json", by_alias=True)) + b"\n"


def jsonl(models: Iterable[BaseModel]) -> bytes:
    return b"".join(line(m) for m in models)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _location(block: Mapping[str, Any]) -> Location:
    found = {k: block[k] for k in _LOCATION_KEYS if block.get(k) is not None}
    for key in ("page", "line"):
        if key in found and not isinstance(found[key], int):
            found.pop(key)
    for key in ("sheet", "cell"):
        if key in found:
            found[key] = str(found[key])
    return Location(**found)


def build_chunks(doc: Doc) -> tuple[list[OkfChunk], list[str]]:
    """One chunk per doc.json block, tiled so the chunks cover doc.md exactly: chunk i runs from
    block i's start to block i+1's (the first from 0, the last to the end). No usable blocks: one
    document-scope chunk. Returns the chunks and warnings (codes + ids)."""
    dvid = doc.document_version_id
    starts: list[int] = []
    pos = 0
    usable = [b for b in doc.blocks if isinstance(b, dict) and isinstance(b.get("text"), str)]
    for block in usable:
        at = doc.text.find(block["text"], pos) if block["text"] else -1
        if at < 0:
            break
        starts.append(at)
        pos = at + len(block["text"])
    warnings: list[str] = []
    if not usable or len(starts) != len(usable):
        if usable:
            warnings.append(f"BLOCKS_NOT_IN_TEXT {dvid}")
        whole = OkfChunk(
            id=f"{dvid}#c0",
            document_version_id=dvid,
            location=Location(scope="document"),
            text=doc.text,
        )
        return [whole], warnings
    starts[0] = 0
    bounds = [*starts, len(doc.text)]
    chunks = []
    for i, block in enumerate(usable):
        chunks.append(
            OkfChunk(
                id=f"{dvid}#c{i}",
                document_version_id=dvid,
                location=_location(block),
                text=doc.text[bounds[i] : bounds[i + 1]],
                ocr="llm" if block.get("ocr") == "llm" else None,
                unreadable=bool(block.get("unreadable")),
            )
        )
    return chunks, warnings


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold().strip()


def _document_of(name: str | None) -> uuid.UUID | None:
    """Cognee names a data item after its file: `<document_version_id>_<sha256>` (E5.1)."""
    try:
        return uuid.UUID(str(name or "").split("_", 1)[0])
    except ValueError:
        return None


@dataclass
class _Graph:
    entities: list[OkfEntity]
    relations: list[OkfRelation]
    dropped: int
    warnings: list[str]
    per_doc: dict[uuid.UUID, int]


def graph_records(
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    chunks_by_doc: Mapping[uuid.UUID, list[OkfChunk]],
) -> _Graph:
    by_id = {n.id: n for n in nodes}
    chunk_doc: dict[str, uuid.UUID] = {}
    chunk_entities: dict[str, set[str]] = {}
    entity_type: dict[str, str] = {}
    warnings: set[str] = set()
    for edge in edges:
        src, dst = by_id.get(edge.source), by_id.get(edge.target)
        if src is None or dst is None:
            continue
        if (
            edge.label == "is_part_of"
            and src.type == "DocumentChunk"
            and dst.type == "TextDocument"
        ):
            dvid = _document_of(dst.name)
            if dvid is None:
                continue
            if dvid not in chunks_by_doc:
                warnings.add(f"{CROSS_PROJECT_REF} {dvid}")  # rejected: not this version's
                continue
            chunk_doc[src.id] = dvid
        elif edge.label == "contains" and dst.type == "Entity":
            chunk_entities.setdefault(src.id, set()).add(dst.id)
        elif edge.label == "is_a" and src.type == "Entity":
            entity_type[src.id] = dst.name or ""
    entity_docs: dict[str, set[uuid.UUID]] = {}
    for chunk_id, entity_ids in chunk_entities.items():
        dvid = chunk_doc.get(chunk_id)
        if dvid is not None:
            for entity_id in entity_ids:
                entity_docs.setdefault(entity_id, set()).add(dvid)

    entities: list[OkfEntity] = []
    refs_of: dict[str, list[SourceRef]] = {}
    dropped = 0
    per_doc: dict[uuid.UUID, int] = {}
    for node in sorted((n for n in nodes if n.type == "Entity"), key=lambda n: n.id):
        name = node.name or ""
        refs: list[SourceRef] = []
        for dvid in sorted(entity_docs.get(node.id, set()), key=str):
            chunks = chunks_by_doc[dvid]
            matched = [c for c in chunks if name and _norm(name) in _norm(c.text)]
            for chunk in matched or chunks[:1]:
                location = chunk.location
                if not matched:  # the LLM renamed it: point at the whole document, flagged
                    location = location.model_copy(update={"scope": "document"})
                refs.append(
                    SourceRef(document_version_id=dvid, chunk_id=chunk.id, location=location)
                )
            per_doc[dvid] = per_doc.get(dvid, 0) + 1
        if not refs:
            dropped += 1  # E5.3: a node without a source ref is dropped and counted
            continue
        refs_of[node.id] = refs
        description = node.properties.get("description")
        entities.append(
            OkfEntity(
                id=node.id,
                type=entity_type.get(node.id) or "Entity",
                name=name,
                attributes={"description": str(description)} if description else {},
                source_refs=refs,
            )
        )

    relations: list[OkfRelation] = []
    seen: set[str] = set()
    for edge in sorted(edges, key=lambda e: (e.source, e.target, e.label)):
        if (
            edge.label in STRUCTURAL_EDGES
            or edge.source not in refs_of
            or edge.target not in refs_of
        ):
            continue
        rid = sha256(canonical_json([edge.source, edge.target, edge.label]))[:32]
        if rid in seen:
            continue
        seen.add(rid)
        targets = {r.document_version_id for r in refs_of[edge.target]}
        shared = [r for r in refs_of[edge.source] if r.document_version_id in targets]
        relation = {
            "id": rid,
            "type": edge.label,
            "from": edge.source,  # a Python keyword: set through the alias
            "to": edge.target,
            "source_refs": shared or refs_of[edge.source],
        }
        relations.append(OkfRelation.model_validate(relation))
    return _Graph(entities, relations, dropped, sorted(warnings), per_doc)


def build_parts(
    docs: list[Doc],
    outcomes: list[CoverageEntry],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    masters: bytes,
) -> Built:
    """Every part but bundle.json (which needs the parts' storage keys)."""
    chunks_by_doc: dict[uuid.UUID, list[OkfChunk]] = {}
    warnings: list[str] = []
    for doc in sorted(docs, key=lambda d: str(d.document_version_id)):
        chunks, notes = build_chunks(doc)
        chunks_by_doc[doc.document_version_id] = chunks
        warnings += notes
    graph = graph_records(nodes, edges, chunks_by_doc)
    documents = []
    for outcome in sorted(outcomes, key=lambda o: str(o.document_version_id)):
        dvid = outcome.document_version_id
        if outcome.status is SourceStatus.FAILED or dvid not in chunks_by_doc:
            documents.append(
                DocumentCoverage(
                    document_version_id=dvid,
                    status=CoverageStatus.FAILED,
                    reason=outcome.reason or "NOT_IN_GRAPH",
                )
            )
            continue
        found = graph.per_doc.get(dvid, 0)
        documents.append(
            DocumentCoverage(
                document_version_id=dvid,
                status=CoverageStatus.COVERED if found else CoverageStatus.CHUNKS_ONLY,
                chunks=len(chunks_by_doc[dvid]),
                entities=found,
            )
        )
    coverage = BundleCoverage(
        documents=documents,
        dropped_entities=graph.dropped,
        warnings=sorted(set(warnings + graph.warnings)),
    )
    all_chunks = [c for dvid in sorted(chunks_by_doc, key=str) for c in chunks_by_doc[dvid]]
    parts = {
        "entities.jsonl": jsonl(graph.entities),
        "relations.jsonl": jsonl(graph.relations),
        "chunks.jsonl": jsonl(all_chunks),
        "masters.jsonl": masters,
        "coverage.json": line(coverage),
    }
    counts = {
        "entities": len(graph.entities),
        "relations": len(graph.relations),
        "chunks": len(all_chunks),
    }
    return Built(parts=parts, coverage=coverage, counts=counts)


def manifest(
    *,
    version_id: uuid.UUID,
    project_id: uuid.UUID,
    number: int,
    docs: list[Doc],
    parts: Mapping[str, PartRef],
    snapshot_id: uuid.UUID | None,
    masters_sha256: str,
) -> bytes:
    body = BundleManifest(
        id=version_id,
        project_id=project_id,
        knowledge_version=number,
        sources=[
            BundleSource(document_version_id=d.document_version_id, sha256=d.sha256)
            for d in sorted(docs, key=lambda d: str(d.document_version_id))
        ],
        master_snapshot_ref=(
            MasterSnapshotRef(snapshot_id=snapshot_id, sha256=masters_sha256)
            if snapshot_id is not None
            else None
        ),
        parts={name: parts[name] for name in PARTS},
    )
    return line(body)


def _lines[M: BaseModel](data: bytes, model: type[M], name: str, problems: list[str]) -> list[M]:
    out: list[M] = []
    for number, raw in enumerate(data.decode("utf-8").splitlines(), start=1):
        try:
            out.append(model.model_validate_json(raw))
        except (ValidationError, ValueError):
            problems.append(f"INVALID_LINE {name}:{number}")
    return out


def validate(
    bundle: bytes,
    parts: Mapping[str, bytes],
    docs: list[Doc],
    expected_documents: set[uuid.UUID],
) -> list[str]:
    """The E5.3 rules over the bytes as written. Empty list: valid."""
    problems: list[str] = []
    try:
        head = BundleManifest.model_validate_json(bundle)
    except ValidationError:
        return [f"INVALID_LINE {BUNDLE}:1"]
    for name in PARTS:
        ref = head.parts.get(name)
        if ref is None or sha256(parts.get(name, b"")) != ref.sha256:
            problems.append(f"HASH_MISMATCH {name}")
    sources = {s.document_version_id: s.sha256 for s in head.sources}
    chunks = _lines(parts.get("chunks.jsonl", b""), OkfChunk, "chunks.jsonl", problems)
    entities = _lines(parts.get("entities.jsonl", b""), OkfEntity, "entities.jsonl", problems)
    relations = _lines(parts.get("relations.jsonl", b""), OkfRelation, "relations.jsonl", problems)
    _lines(parts.get("masters.jsonl", b""), MasterNode, "masters.jsonl", problems)
    chunk_ids = {c.id for c in chunks}
    for doc in docs:
        joined = "".join(c.text for c in chunks if c.document_version_id == doc.document_version_id)
        if sha256(joined.encode("utf-8")) != doc.sha256:
            problems.append(f"TEXT_NOT_PRESERVED {doc.document_version_id}")
    entity_ids = {e.id for e in entities}
    for relation in relations:
        if relation.from_ not in entity_ids or relation.to not in entity_ids:
            problems.append(f"DANGLING_RELATION {relation.id}")
    records: list[OkfEntity | OkfRelation] = [*entities, *relations]
    for record in records:
        for source in record.source_refs:
            if source.document_version_id not in sources:
                problems.append(f"{CROSS_PROJECT_REF} {record.id}")
            elif source.chunk_id not in chunk_ids:
                problems.append(f"UNRESOLVED_REF {record.id}")
    try:
        coverage = BundleCoverage.model_validate_json(parts.get("coverage.json", b"{}"))
    except ValidationError:
        return [*problems, "INVALID_LINE coverage.json:1"]
    listed = {d.document_version_id for d in coverage.documents}
    if listed != expected_documents:
        problems.append("COVERAGE_MISMATCH")
    if not any(d.status is not CoverageStatus.FAILED for d in coverage.documents):
        problems.append(ZERO_COVERAGE)
    return problems


def parse_blocks(doc_json: bytes | None) -> list[dict[str, Any]]:
    """`blocks` of an E4.5 doc.json; anything unreadable counts as no blocks."""
    if not doc_json:
        return []
    try:
        data = json.loads(doc_json)
    except ValueError:
        return []
    blocks = data.get("blocks") if isinstance(data, dict) else None
    return [b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else []
