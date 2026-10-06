"""OKF bundle exporter prototype (TD §5.3, issue #26). Pure functions: no Cognee or network imports.

Inputs : Cognee graph (nodes, edges), normalized derivatives (doc.md + doc.json), master rows (E3.6 shape).
Output : okf-bundle/{bundle.json, entities.jsonl, relations.jsonl, chunks.jsonl, masters.jsonl, coverage.json}

Design points the spike validates:
  * chunks.jsonl is built from the normalized documents, NOT from Cognee's chunker, so full content is
    preserved even if graph extraction misses facts (C-13). Joined chunk text == doc.md byte for byte.
  * Entities/relations come from the graph; every record must carry >=1 source_ref (document_version_id + location).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEP = "\n\n"
OKF_VERSION = "0.1-spike"
STRUCTURAL_EDGES = {"is_part_of", "contains", "made_from", "is_a", "exists_in", "has_summary"}
PARTS = ("entities.jsonl", "relations.jsonl", "chunks.jsonl", "masters.jsonl", "coverage.json")


@dataclass(frozen=True)
class Document:
    document_version_id: str
    name: str
    title: str
    text: str
    blocks: list[dict[str, Any]]

    @staticmethod
    def load(folder: Path) -> "Document":
        meta = json.loads((folder / "doc.json").read_text(encoding="utf-8"))
        text = (folder / "doc.md").read_bytes().decode("utf-8")
        return Document(meta["document_version_id"], meta["name"], meta["title"], text, meta["blocks"])


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical(r) + b"\n" for r in rows)


def build_chunks(doc: Document) -> list[dict[str, Any]]:
    """One chunk per block, tiled by position so the chunks cover doc.md exactly (any separator, any trailing text).

    Chunk i spans from the start of block i to the start of block i+1; the last chunk runs to the end of the
    document and the first starts at 0. A block that cannot be located in order breaks the loader contract.
    """
    starts: list[int] = []
    pos = 0
    for b in doc.blocks:
        at = doc.text.find(b["text"], pos)
        if at < 0:
            raise ValueError(f"block {b['id']} not found in order in the normalized text")
        starts.append(at)
        pos = at + len(b["text"])
    starts[0] = 0
    bounds = starts + [len(doc.text)]
    return [{"id": f"{doc.document_version_id}#c{i}", "document_version_id": doc.document_version_id,
             "location": b["location"], "text": doc.text[bounds[i]:bounds[i + 1]]}
            for i, b in enumerate(doc.blocks)]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).casefold().strip()


def build_graph_records(nodes: list[tuple[str, dict]], edges: list[tuple], docs: dict[str, Document],
                        chunks_by_doc: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Entities and relations with provenance. Returns records plus per-document coverage inputs."""
    by_id = {n[0]: n[1] for n in nodes}
    chunk_to_doc: dict[str, str] = {}
    chunk_entities: dict[str, set[str]] = {}
    entity_type: dict[str, str] = {}
    for src, dst, label, *_ in edges:
        if label == "is_part_of" and by_id.get(src, {}).get("type") == "DocumentChunk" \
                and by_id.get(dst, {}).get("type") == "TextDocument":
            chunk_to_doc[src] = by_id[dst]["name"]  # staged file stem == document_version_id
        elif label == "contains" and by_id.get(dst, {}).get("type") == "Entity":
            chunk_entities.setdefault(src, set()).add(dst)
        elif label == "is_a" and by_id.get(src, {}).get("type") == "Entity":
            entity_type[src] = by_id.get(dst, {}).get("name", "")

    entity_docs: dict[str, set[str]] = {}
    for chunk_id, ents in chunk_entities.items():
        dvid = chunk_to_doc.get(chunk_id)
        if dvid in docs:
            for e in ents:
                entity_docs.setdefault(e, set()).add(dvid)

    entities, stats = [], {"block_level": 0, "document_level_fallback": 0, "no_ref_dropped": 0}
    refs_of: dict[str, list[dict[str, Any]]] = {}
    for nid, props in sorted(((i, p) for i, p in by_id.items() if p.get("type") == "Entity"), key=lambda x: x[0]):
        name = props.get("name", "")
        refs: list[dict[str, Any]] = []
        for dvid in sorted(entity_docs.get(nid, ())):
            matched = [c for c in chunks_by_doc[dvid] if name and _norm(name) in _norm(c["text"])]
            if matched:
                refs += [{"document_version_id": dvid, "chunk_id": c["id"], "location": c["location"]} for c in matched]
                stats["block_level"] += 1
            else:  # name normalized by the LLM; fall back to the whole document, flagged
                first = chunks_by_doc[dvid][0]
                refs.append({"document_version_id": dvid, "chunk_id": first["id"],
                             "location": {"scope": "document", **first["location"]}})
                stats["document_level_fallback"] += 1
        if not refs:
            stats["no_ref_dropped"] += 1  # E5.3 edge case: node without source ref is dropped, counted
            continue
        refs_of[nid] = refs
        entities.append({"id": nid, "type": entity_type.get(nid, "Entity"), "name": name,
                         "attributes": {"description": props.get("description", "")}, "source_refs": refs})

    relations = []
    for src, dst, label, *rest in sorted(edges, key=lambda e: (str(e[0]), str(e[1]), str(e[2]))):
        if label in STRUCTURAL_EDGES or src not in refs_of or dst not in refs_of:
            continue
        shared = [r for r in refs_of[src] if any(r["document_version_id"] == q["document_version_id"] for q in refs_of[dst])]
        relations.append({"id": sha256(canonical([str(src), str(dst), label]))[:32], "type": label,
                          "from": src, "to": dst, "attributes": {}, "source_refs": shared or refs_of[src]})
    return {"entities": entities, "relations": relations, "stats": stats, "entity_docs": entity_docs}


def build_coverage(docs: dict[str, Document], chunks_by_doc: dict[str, list], entities: list[dict[str, Any]],
                   failed: dict[str, str]) -> dict[str, Any]:
    per_doc: dict[str, int] = {}
    for e in entities:
        for dvid in {r["document_version_id"] for r in e["source_refs"]}:
            per_doc[dvid] = per_doc.get(dvid, 0) + 1
    out = {}
    for dvid in sorted(docs):
        if dvid in failed:
            out[dvid] = {"chunks": 0, "entities": 0, "status": "FAILED", "reason": failed[dvid]}
        else:
            n = per_doc.get(dvid, 0)
            out[dvid] = {"chunks": len(chunks_by_doc[dvid]), "entities": n,
                         "status": "COVERED" if n else "CHUNKS_ONLY"}
    return {"documents": out}


def write_bundle(out_dir: Path, *, project_id: str, workflow_id: str | None, knowledge_version: int,
                 docs: dict[str, Document], graph_nodes: list, graph_edges: list, master_rows: list[dict[str, Any]],
                 master_snapshot_ref: dict[str, Any], failed: dict[str, str] | None = None) -> dict[str, Any]:
    failed = failed or {}
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_by_doc = {d: build_chunks(doc) for d, doc in docs.items()}
    g = build_graph_records(graph_nodes, graph_edges, docs, chunks_by_doc)
    coverage = build_coverage(docs, chunks_by_doc, g["entities"], failed)
    chunk_rows = [c for d in sorted(chunks_by_doc) for c in chunks_by_doc[d]]
    files = {"entities.jsonl": jsonl(g["entities"]), "relations.jsonl": jsonl(g["relations"]),
             "chunks.jsonl": jsonl(chunk_rows), "masters.jsonl": jsonl(master_rows),
             "coverage.json": canonical(coverage) + b"\n"}
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    manifest = {"id": sha256(canonical([project_id, knowledge_version]))[:32], "project_id": project_id,
                "workflow_id": workflow_id, "knowledge_version": knowledge_version, "okf_version": OKF_VERSION,
                "sources": [{"document_version_id": d, "sha256": sha256(docs[d].text.encode("utf-8")),
                             "title": docs[d].title, "type": "md"} for d in sorted(docs)],
                "master_snapshot_ref": master_snapshot_ref,
                "parts": {n: sha256(files[n]) for n in PARTS}}
    # created_at is intentionally omitted from the hashed manifest: it would break rebuild determinism.
    manifest["okf_sha256"] = sha256(canonical(manifest))
    (out_dir / "bundle.json").write_bytes(canonical(manifest) + b"\n")
    return {"manifest": manifest, "graph_stats": g["stats"], "coverage": coverage,
            "counts": {"entities": len(g["entities"]), "relations": len(g["relations"]), "chunks": len(chunk_rows),
                       "masters": len(master_rows)}}


def verify_bundle(bundle_dir: Path, docs: dict[str, Document]) -> list[str]:
    """Validation rules from E5.3. Returns a list of problems (empty == valid)."""
    problems: list[str] = []
    manifest = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    for name in PARTS:
        if sha256((bundle_dir / name).read_bytes()) != manifest["parts"][name]:
            problems.append(f"HASH_MISMATCH {name}")
    body = {k: v for k, v in manifest.items() if k != "okf_sha256"}
    if sha256(canonical(body)) != manifest["okf_sha256"]:
        problems.append("HASH_MISMATCH bundle.json")
    source_ids = {s["document_version_id"] for s in manifest["sources"]}
    chunks = [json.loads(line) for line in (bundle_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    chunk_ids = {c["id"] for c in chunks}
    entities = [json.loads(line) for line in (bundle_dir / "entities.jsonl").read_text(encoding="utf-8").splitlines()]
    entity_ids = {e["id"] for e in entities}
    for r in (json.loads(line) for line in (bundle_dir / "relations.jsonl").read_text(encoding="utf-8").splitlines()):
        if r["from"] not in entity_ids or r["to"] not in entity_ids:
            problems.append(f"DANGLING_RELATION {r['id']}")
    for rec in entities:
        if not rec["source_refs"]:
            problems.append(f"NO_SOURCE_REF {rec['id']}")
        for ref in rec["source_refs"]:
            if ref["document_version_id"] not in source_ids or ref["chunk_id"] not in chunk_ids:
                problems.append(f"UNRESOLVED_REF {rec['id']}")
    for dvid, doc in docs.items():
        joined = "".join(c["text"] for c in chunks if c["document_version_id"] == dvid)
        if joined != doc.text:
            problems.append(f"TEXT_MISMATCH {dvid}")
    return problems
