"""E5.3 OKF bundle: build, validate, write, READY (a fake Cognee with its graph shape, SQLite)."""

import hashlib
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from knowledge_db import TENANT, World, versions
from sbam_api.core.conventions import ApiError
from sbam_api.knowledge.service import read_okf_part
from sbam_api.storage.base import Kind, StorageScope
from sbam_contracts.knowledge import CoverageEntry, GraphEdge, GraphNode, SourceStatus
from sbam_contracts.okf import PARTS, BundleCoverage, BundleManifest, CoverageStatus, PartRef
from sbam_workers.knowledge import okf
from temporalio.exceptions import ApplicationError

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas" / "okf"
PART_SCHEMA = {
    "entities.jsonl": "entity",
    "relations.jsonl": "relation",
    "chunks.jsonl": "chunk",
    "coverage.json": "coverage",
}


@pytest.fixture
def w(tmp_path: Path) -> Iterator[World]:
    yield World(tmp_path)


def manifest_of(w: World, version: uuid.UUID) -> tuple[BundleManifest, dict[str, bytes], bytes]:
    [row] = [v for v in versions(w.sessions, w.project) if v.id == version]
    assert row.okf_bundle_key is not None
    bundle = w.storage.get(row.okf_bundle_key, verify=True)
    head = BundleManifest.model_validate_json(bundle)
    parts = {name: w.storage.get(ref.storage_key, verify=True) for name, ref in head.parts.items()}
    return head, parts, bundle


def coverage_of(parts: dict[str, bytes]) -> BundleCoverage:
    return BundleCoverage.model_validate_json(parts["coverage.json"])


def rows(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(x) for x in data.decode("utf-8").splitlines()]


def test_e5_3_t01_valid_bundle_six_parts_schema_valid_ready(w: World) -> None:
    w.docs("Supplier Mouser quoted ZEBRA-7731.", "BOM for PCBA by Acme.", "SOP for Soldering.")
    kv = w.build_all(w.run_input())
    [row] = versions(w.sessions, w.project)
    assert row.state == "READY"
    head, parts, bundle = manifest_of(w, kv)
    assert set(parts) == set(PARTS) and len(parts) + 1 == 6  # five parts + bundle.json
    assert row.okf_sha256 == hashlib.sha256(bundle).hexdigest()
    jsonschema.validate(
        json.loads(bundle), json.loads((SCHEMAS / "bundle.schema.json").read_text())
    )
    for name, stem in PART_SCHEMA.items():
        schema = json.loads((SCHEMAS / f"{stem}.schema.json").read_text())
        for record in rows(parts[name]):
            jsonschema.validate(record, schema)
    for name, ref in head.parts.items():
        assert ref.sha256 == hashlib.sha256(parts[name]).hexdigest()
    assert (head.id, head.knowledge_version, head.workflow_id) == (kv, 1, None)
    assert len(head.sources) == 3


def test_e5_3_t02_full_text_preserved_byte_for_byte(w: World) -> None:
    table = "| Part | Qty |\n|---|---|\n| Résistance 10kΩ | 500 |\n"
    text = "# Spec ZEBRA-7731\n\n" + "مرحبا بالعالم — 日本語のテキスト. " * 40000 + "\n\n" + table
    blocks = [
        {"type": "heading", "text": "# Spec ZEBRA-7731", "page": 1},
        {"type": "paragraph", "text": "مرحبا بالعالم", "page": 1},
        {"type": "table", "text": "| Part | Qty |", "sheet": "BOM", "cell": "A1"},
    ]
    assert len(text.encode()) > 2_000_000
    [dv] = w.docs(text, blocks=blocks)
    kv = w.build_all(w.run_input())
    _, parts, _ = manifest_of(w, kv)
    chunks = [c for c in rows(parts["chunks.jsonl"]) if c["document_version_id"] == str(dv)]
    joined = "".join(c["text"] for c in chunks)
    assert hashlib.sha256(joined.encode()).digest() == hashlib.sha256(text.encode()).digest()
    assert [c["location"].get("page") for c in chunks][:2] == [1, 1]
    assert chunks[2]["location"]["sheet"] == "BOM"


def test_e5_3_t03_coverage_lists_every_document_with_the_failed_one(w: World) -> None:
    docs = w.docs("Mouser A", "Arrow B", "broken C", "Digikey D", "plain text only")
    w.cognee.refuse.add(str(docs[2]))
    kv = w.build_all(w.run_input())
    _, parts, _ = manifest_of(w, kv)
    status = {d.document_version_id: d for d in coverage_of(parts).documents}
    assert set(status) == set(docs)
    assert status[docs[2]].status is CoverageStatus.FAILED and status[docs[2]].reason
    assert status[docs[4]].status is CoverageStatus.CHUNKS_ONLY  # no entity, still covered
    assert {status[d].status for d in (docs[0], docs[1], docs[3])} == {CoverageStatus.COVERED}


def bundle_for(parts: dict[str, bytes], docs: list[okf.Doc]) -> bytes:
    refs = {
        n: PartRef(storage_key=f"t/p/okf/{okf.sha256(b)}", sha256=okf.sha256(b))
        for n, b in parts.items()
    }
    return okf.manifest(
        version_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        number=1,
        docs=docs,
        parts=refs,
        snapshot_id=None,
        masters_sha256=okf.sha256(b""),
    )


def small() -> tuple[okf.Doc, list[CoverageEntry], list[GraphNode], list[GraphEdge]]:
    dvid = uuid.uuid4()
    text = "Mouser ships Resistors."
    doc = okf.Doc(dvid, text, hashlib.sha256(text.encode()).hexdigest())
    outcomes = [CoverageEntry(document_version_id=dvid, status=SourceStatus.ADDED)]
    nodes = [
        GraphNode(id="d", type="TextDocument", name=f"{dvid}_{'0' * 64}"),
        GraphNode(id="c", type="DocumentChunk"),
        GraphNode(id="e1", type="Entity", name="Mouser"),
        GraphNode(id="e2", type="Entity", name="Resistors"),
    ]
    edges = [
        GraphEdge(source="c", target="d", label="is_part_of"),
        GraphEdge(source="c", target="e1", label="contains"),
        GraphEdge(source="c", target="e2", label="contains"),
        GraphEdge(source="e1", target="e2", label="ships"),
    ]
    return doc, outcomes, nodes, edges


def test_e5_3_t04_dangling_relation_is_bundle_invalid() -> None:
    doc, outcomes, nodes, edges = small()
    built = okf.build_parts([doc], outcomes, nodes, edges, b"")
    lines = rows(built.parts["relations.jsonl"])
    lines[0]["to"] = "missing-entity"
    built.parts["relations.jsonl"] = b"".join(json.dumps(x).encode() + b"\n" for x in lines)
    problems = okf.validate(
        bundle_for(built.parts, [doc]), built.parts, [doc], {doc.document_version_id}
    )
    assert f"DANGLING_RELATION {lines[0]['id']}" in problems


def test_e5_3_t05_every_source_ref_resolves_to_text_with_the_entity(w: World) -> None:
    blocks = [
        {"type": "paragraph", "text": "Supplier Mouser quoted ZEBRA-7731.", "page": 1},
        {"type": "paragraph", "text": "Acme needs Soldering.", "page": 2},
    ]
    w.docs("Supplier Mouser quoted ZEBRA-7731. Acme needs Soldering.", blocks=blocks)
    kv = w.build_all(w.run_input())
    _, parts, _ = manifest_of(w, kv)
    chunks = {c["id"]: c for c in rows(parts["chunks.jsonl"])}
    entities = rows(parts["entities.jsonl"])
    assert entities
    for entity in entities[:20]:
        for ref in entity["source_refs"]:
            chunk = chunks[ref["chunk_id"]]
            assert chunk["document_version_id"] == ref["document_version_id"]
            assert entity["name"].casefold() in chunk["text"].casefold()
            assert ref["location"]["page"] in (1, 2)


def test_e5_3_t06_tampered_part_is_refused_with_hash_mismatch(
    w: World, caplog: pytest.LogCaptureFixture
) -> None:
    w.docs("Mouser")
    kv = w.build_all(w.run_input())
    head, _, _ = manifest_of(w, kv)
    key = head.parts["chunks.jsonl"].storage_key
    path = Path(w.storage.root) / key
    data = bytearray(path.read_bytes())
    data[0] ^= 1
    path.chmod(0o644)  # stored objects are read-only
    path.write_bytes(bytes(data))
    with pytest.raises(ApiError) as err:
        read_okf_part(w.storage, key)
    assert (err.value.status, err.value.code) == (409, "HASH_MISMATCH")
    assert any(getattr(r, "alert", None) == "okf_tampered" for r in caplog.records)


def test_e5_3_t07_zero_coverage_is_non_retryable_and_failed(w: World) -> None:
    doc, _, nodes, edges = small()
    failed = [
        CoverageEntry(document_version_id=doc.document_version_id, status=SourceStatus.FAILED)
    ]
    built = okf.build_parts([], failed, nodes, edges, b"")
    problems = okf.validate(bundle_for(built.parts, []), built.parts, [], {doc.document_version_id})
    assert okf.ZERO_COVERAGE in problems
    # through the activity: a version whose every document failed
    [one] = w.docs("Mouser")
    inp = w.run_input()
    kv = w.build(inp)
    with w.sessions() as s:
        from sbam_api.knowledge.models import KnowledgeSource

        for source in s.query(KnowledgeSource).filter_by(knowledge_version_id=kv):
            source.status = "FAILED"
        s.commit()
    with pytest.raises(ApplicationError) as err:
        w.bundle(inp, kv)
    assert err.value.type == "BundleInvalid" and err.value.non_retryable
    [row] = versions(w.sessions, w.project)
    assert (row.state, row.error_reason, row.okf_bundle_key) == ("FAILED", "BundleInvalid", None)
    assert one


def test_e5_3_t08_rebuilding_the_same_version_gives_the_same_bytes(w: World) -> None:
    w.docs("Mouser quoted ZEBRA-7731.", "Arrow quoted cables.")
    inp = w.run_input()
    kv = w.build(inp)
    snapshot = sorted(
        p.name for p in (Path(w.storage.root) / str(TENANT)).rglob("*") if p.is_file()
    )
    w.bundle(inp, kv)
    first = versions(w.sessions, w.project)[0].okf_sha256
    # the activity retried after a partial write: same parts, same keys, nothing duplicated
    with w.sessions() as s:
        from sbam_api.knowledge.models import KnowledgeVersion
        from sqlalchemy import update

        s.execute(update(KnowledgeVersion).values(state="BUILDING"))
        s.commit()
    files_before = sorted(p.name for p in Path(w.storage.root).rglob("*") if p.is_file())
    w.bundle(inp, kv)
    files_after = sorted(p.name for p in Path(w.storage.root).rglob("*") if p.is_file())
    assert versions(w.sessions, w.project)[0].okf_sha256 == first
    assert files_after == files_before and len(files_after) > len(snapshot)


def test_e5_3_t09_cross_project_reference_is_rejected() -> None:
    doc, outcomes, nodes, edges = small()
    stranger = uuid.uuid4()  # a document version of another project
    nodes += [
        GraphNode(id="d2", type="TextDocument", name=f"{stranger}_{'1' * 64}"),
        GraphNode(id="c2", type="DocumentChunk"),
        GraphNode(id="e3", type="Entity", name="Secret"),
    ]
    edges += [
        GraphEdge(source="c2", target="d2", label="is_part_of"),
        GraphEdge(source="c2", target="e3", label="contains"),
    ]
    built = okf.build_parts([doc], outcomes, nodes, edges, b"")
    assert f"CROSS_PROJECT_REF {stranger}" in built.coverage.warnings
    ids = [e["id"] for e in rows(built.parts["entities.jsonl"])]
    assert "e3" not in ids and built.coverage.dropped_entities == 1
    assert str(stranger) not in built.parts["chunks.jsonl"].decode()


def test_e5_3_document_without_doc_json_is_one_document_chunk() -> None:
    doc = okf.Doc(uuid.uuid4(), "plain", hashlib.sha256(b"plain").hexdigest())
    chunks, warnings = okf.build_chunks(doc)
    assert [(c.text, c.location.scope) for c in chunks] == [("plain", "document")]
    assert warnings == []


def test_e5_3_ocr_unreadable_block_is_flagged() -> None:
    text = "page one\n\n[unreadable]"
    doc = okf.Doc(
        uuid.uuid4(),
        text,
        hashlib.sha256(text.encode()).hexdigest(),
        [
            {"text": "page one", "page": 1},
            {"text": "[unreadable]", "page": 2, "ocr": "llm", "unreadable": True},
        ],
    )
    chunks, _ = okf.build_chunks(doc)
    assert (chunks[1].ocr, chunks[1].unreadable) == ("llm", True)
    assert "".join(c.text for c in chunks) == text


def test_e5_3_ready_version_is_returned_as_it_is(w: World) -> None:
    w.docs("Mouser")
    inp = w.run_input()
    kv = w.build_all(inp)
    assert w.bundle(inp, kv) == kv  # a retry after READY: nothing rewritten
    assert w.cognee.calls.count("graph") == 2  # build_knowledge's count + the first bundle only


def test_e5_3_storage_keys_are_content_addressed_under_okf(w: World) -> None:
    w.docs("Mouser")
    kv = w.build_all(w.run_input())
    head, parts, _ = manifest_of(w, kv)
    where = StorageScope(TENANT, w.project)
    for name, ref in head.parts.items():
        assert ref.storage_key == where.key(Kind.OKF, hashlib.sha256(parts[name]).hexdigest())
