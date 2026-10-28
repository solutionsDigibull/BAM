"""E5.4 graph read API through the real app and auth chain, over a real OKF bundle in storage."""

import hashlib
import io
import json
import re
import time
import uuid
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr
from sbam_api.core.conventions import content_disposition
from sbam_api.core.ids import uuid7
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.knowledge import graph as graph_module
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.knowledge import CoverageEntry, GraphEdge, GraphNode, SourceStatus
from sbam_contracts.okf import PARTS, PartRef
from sbam_workers.knowledge import okf
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_knowledge_api import Harness as KnowledgeHarness

SIGNING_KEY = "k" * 40
LONG = "Long passage. " * 400  # > 4000 characters


class Harness(KnowledgeHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.root = tmp_path
        state = self.client.app.state  # type: ignore[attr-defined]
        state.settings.storage_signing_key = SecretStr(SIGNING_KEY)
        state.storage = None  # built from the settings on first use
        self.storage = LocalDiskBackend(tmp_path)
        graph_module.limiter = graph_module.RateLimiter()  # a fresh budget per test
        self.client.follow_redirects = False  # see the 302s

    def get(self, path: str, token: str | None = None, **params: Any) -> Any:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"/projects/{self.project}/knowledge{path}" + (f"?{query}" if query else "")
        return self.call("GET", url, token or self.fde())

    def document(self, text: str, title: str = "rfq.pdf") -> tuple[uuid.UUID, okf.Doc]:
        project = uuid.UUID(self.project)
        with self.sessions() as s:
            doc = Document(tenant_id=TENANT_A, project_id=project, title=title)
            s.add(doc)
            s.flush()
            version = DocumentVersion(
                tenant_id=TENANT_A,
                project_id=project,
                document_id=doc.id,
                storage_key="k",
                sha256=hashlib.sha256(text.encode()).hexdigest(),
                size=len(text),
                mime="application/pdf",
                state="PARSED",
                scan_status="CLEAN",
            )
            s.add(version)
            s.commit()
            dvid = version.id
        return dvid, okf.Doc(dvid, text, hashlib.sha256(text.encode()).hexdigest())

    def ready(
        self,
        docs: list[okf.Doc],
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        number: int = 1,
        state: str = "READY",
    ) -> uuid.UUID:
        outcomes = [
            CoverageEntry(document_version_id=d.document_version_id, status=SourceStatus.ADDED)
            for d in docs
        ]
        built = okf.build_parts(docs, outcomes, nodes, edges, b"")
        where = StorageScope(TENANT_A, uuid.UUID(self.project))
        refs = {}
        for name, data in built.parts.items():
            stored = self.storage.put(data, where, Kind.OKF, "application/json")
            refs[name] = PartRef(storage_key=stored.storage_key, sha256=stored.sha256)
        kv = uuid7()
        bundle = okf.manifest(
            version_id=kv,
            project_id=uuid.UUID(self.project),
            number=number,
            docs=docs,
            parts=refs,
            snapshot_id=None,
            masters_sha256=okf.sha256(b""),
        )
        stored = self.storage.put(bundle, where, Kind.OKF, "application/json")
        with self.sessions() as s:
            s.add(
                KnowledgeVersion(
                    id=kv,
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    version=number,
                    run_id=uuid7(),
                    cognee_dataset="d",
                    coverage={},
                    state=state,
                    okf_bundle_key=stored.storage_key,
                    okf_sha256=stored.sha256,
                )
            )
            s.commit()
        return kv


def graph_of(
    dvid: uuid.UUID, entities: list[tuple[str, str, str]], links: Sequence[tuple[str, str]] = ()
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Cognee's shape: one TextDocument, one chunk, entities (id, name, type) and links."""
    nodes = [
        GraphNode(id="doc", type="TextDocument", name=f"{dvid}_{'0' * 64}"),
        GraphNode(id="chunk", type="DocumentChunk"),
    ]
    edges = [GraphEdge(source="chunk", target="doc", label="is_part_of")]
    for entity_id, name, kind in entities:
        nodes += [
            GraphNode(id=entity_id, type="Entity", name=name),
            GraphNode(id=f"type-{kind}", type="EntityType", name=kind),
        ]
        edges += [
            GraphEdge(source="chunk", target=entity_id, label="contains"),
            GraphEdge(source=entity_id, target=f"type-{kind}", label="is_a"),
        ]
    edges += [GraphEdge(source=a, target=b, label="related_to") for a, b in links]
    return list({n.id: n for n in nodes}.values()), edges


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def big(h: Harness, count: int) -> uuid.UUID:
    dvid, doc = h.document("Entities of a large fixture.")
    entities = [(f"e{i:05d}", f"Entity {i}", "Concept") for i in range(count)]
    nodes, edges = graph_of(
        dvid, entities, [(f"e{i:05d}", f"e{i + 1:05d}") for i in range(count - 1)]
    )
    return h.ready([doc], nodes, edges)


def test_e5_4_t01_page_through_1250_nodes(h: Harness) -> None:
    kv = big(h, 1250)
    seen: list[str] = []
    sizes = []
    cursor = None
    for _ in range(5):
        params: dict[str, Any] = {"limit": 500, **({"cursor": cursor} if cursor else {})}
        res = h.get(f"/{kv}/graph", **params)
        assert res.status_code == 200, res.text
        body = res.json()
        sizes.append(len(body["nodes"]))
        seen += [n["id"] for n in body["nodes"]]
        ids = {n["id"] for n in body["nodes"]}
        assert all(e["from"] in ids for e in body["edges"])
        assert all(e["external"] == (e["to"] not in ids) for e in body["edges"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert sizes == [500, 500, 250]
    assert len(seen) == len(set(seen)) == 1250 and seen == sorted(seen)
    assert body["total_estimate"] == 1250 and body["status"] == "READY"


def test_e5_4_t02_search_and_type_filter(h: Harness) -> None:
    dvid, doc = h.document("Invoice approval process and invoice archive.")
    nodes, edges = graph_of(
        dvid,
        [
            ("p1", "Invoice approval", "Process"),
            ("p2", "Shipping", "Process"),
            ("c1", "Invoice archive", "System"),
        ],
    )
    kv = h.ready([doc], nodes, edges)
    body = h.get(f"/{kv}/graph", q="invoice", type="Process").json()
    assert [(n["id"], n["type"]) for n in body["nodes"]] == [("p1", "Process")]
    body = h.get(f"/{kv}/graph", q="%20%20").json()  # whitespace only: no query
    assert len(body["nodes"]) == 3


@pytest.mark.parametrize("limit", [0, 501])
def test_e5_4_t03_invalid_limit_is_422_on_limit(h: Harness, limit: int) -> None:
    kv = big(h, 3)
    res = h.get(f"/{kv}/graph", limit=limit)
    assert res.status_code == 422
    assert any("limit" in str(e.get("path", e)) for e in res.json()["errors"])


def test_e5_4_t04_other_tenants_version_is_404_without_data(h: Harness) -> None:
    kv = big(h, 3)
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    res = h.get(f"/{kv}/graph", token=other)
    problem(res, 404, "NOT_FOUND")
    assert "Entity" not in res.text
    # a version id of another project of the same tenant is not found either
    problem(h.get(f"/{uuid7()}/graph"), 404, "NOT_FOUND")


def test_e5_4_t05_unassigned_fde_is_404(h: Harness) -> None:
    kv = big(h, 3)
    for path in ("", f"/{kv}/graph", f"/{kv}/nodes/e00000", f"/{kv}/okf-bundle"):
        problem(h.get(path, token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")


def test_e5_4_t06_node_detail_with_neighbours_and_sources(h: Harness) -> None:
    dvid, doc = h.document("Supplier Mouser quoted ZEBRA-7731 to Acme.", title="RFQ-12.pdf")
    nodes, edges = graph_of(
        dvid,
        [("m", "Mouser", "Supplier"), ("z", "ZEBRA-7731", "Part"), ("a", "Acme", "Client")],
        [("m", "z"), ("a", "m")],
    )
    kv = h.ready([doc], nodes, edges)
    body = h.get(f"/{kv}/nodes/m").json()
    assert body["node"] == {"id": "m", "type": "Supplier", "name": "Mouser", "degree": 2}
    assert {n["id"] for n in body["neighbours"]} == {"a", "z"}
    [passage] = body["source_passages"]
    assert "Mouser" in passage["text"] and passage["title"] == "RFQ-12.pdf"
    assert passage["document_version_id"] == str(dvid)
    problem(h.get(f"/{kv}/nodes/nope"), 404, "NOT_FOUND")
    assert h.get(f"/{kv}/nodes/bad%20id").status_code == 422


def test_e5_4_passages_are_truncated_at_4000_characters(h: Harness) -> None:
    dvid, doc = h.document("Mouser " + LONG)
    nodes, edges = graph_of(dvid, [("m", "Mouser", "Supplier")])
    kv = h.ready([doc], nodes, edges)
    [passage] = h.get(f"/{kv}/nodes/m").json()["source_passages"]
    assert len(passage["text"]) == 4000 and passage["truncated"] is True


def test_e5_4_more_than_100_neighbours_sets_has_more(h: Harness) -> None:
    dvid, doc = h.document("Hub and spokes.")
    spokes = [(f"s{i:03d}", f"Spoke {i}", "Thing") for i in range(120)]
    nodes, edges = graph_of(
        dvid, [("hub", "Hub", "Thing"), *spokes], [("hub", s[0]) for s in spokes]
    )
    kv = h.ready([doc], nodes, edges)
    body = h.get(f"/{kv}/nodes/hub").json()
    assert len(body["neighbours"]) == 100 and body["has_more"] is True


@pytest.mark.parametrize("state", ["BUILDING", "FAILED"])
def test_e5_4_t07_not_ready_is_409(h: Harness, state: str) -> None:
    kv = h.add_version(1, state)
    problem(h.get(f"/{kv}/graph"), 409, "KNOWLEDGE_NOT_READY")
    problem(h.get(f"/{kv}/nodes/x"), 409, "KNOWLEDGE_NOT_READY")
    problem(h.get(f"/{kv}/okf-bundle"), 409, "KNOWLEDGE_NOT_READY")


def test_e5_4_outdated_version_stays_readable(h: Harness) -> None:
    dvid, doc = h.document("Mouser.")
    nodes, edges = graph_of(dvid, [("m", "Mouser", "Supplier")])
    kv = h.ready([doc], nodes, edges, state="OUTDATED")
    assert h.get(f"/{kv}/graph").json()["status"] == "OUTDATED"


def test_e5_4_t08_query_injection_is_literal_text(h: Harness) -> None:
    kv = big(h, 5)
    payload = "') MATCH (n) DETACH DELETE n //"
    from urllib.parse import quote

    res = h.get(f"/{kv}/graph", q=quote(payload))
    assert res.status_code == 200 and res.json()["nodes"] == []
    assert h.get(f"/{kv}/graph").json()["total_estimate"] == 5  # nothing changed


def test_e5_4_cursor_of_another_version_or_filter_or_edited_is_400(h: Harness) -> None:
    kv1 = big(h, 10)
    dvid, doc = h.document("Other.")
    nodes, edges = graph_of(dvid, [(f"x{i}", f"X{i}", "T") for i in range(10)])
    kv2 = h.ready([doc], nodes, edges, number=2)
    cursor = h.get(f"/{kv1}/graph", limit=2).json()["next_cursor"]
    problem(h.get(f"/{kv2}/graph", limit=2, cursor=cursor), 400, "INVALID_CURSOR")
    problem(h.get(f"/{kv1}/graph", limit=2, cursor=cursor, q="Entity"), 400, "INVALID_CURSOR")
    token, sig = cursor.split(".")
    problem(
        h.get(f"/{kv1}/graph", limit=2, cursor=f"{token}.{'0' * len(sig)}"), 400, "INVALID_CURSOR"
    )
    problem(h.get(f"/{kv1}/graph", cursor="garbage"), 400, "INVALID_CURSOR")


def test_e5_4_versions_list_newest_first_and_only_this_project(h: Harness) -> None:
    big(h, 2)
    h.add_version(2, "BUILDING")
    body = h.get("").json()
    assert [(v["version"], v["status"]) for v in body["versions"]] == [
        (2, "BUILDING"),
        (1, "READY"),
    ]


def test_e5_4_okf_download_is_a_signed_zip_of_the_six_parts(h: Harness) -> None:
    kv = big(h, 3)
    res = h.get(f"/{kv}/okf-bundle")
    assert res.status_code == 302
    url = urlsplit(res.headers["location"])
    params = parse_qs(url.query)
    assert int(params["exp"][0]) - time.time() <= 300
    download = h.client.get(f"{url.path}?{url.query}")
    assert download.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(download.content))
    names = sorted(archive.namelist())
    assert names == sorted(["bundle.json", *PARTS])
    head = json.loads(archive.read("bundle.json"))
    for name, ref in head["parts"].items():
        assert hashlib.sha256(archive.read(name)).hexdigest() == ref["sha256"]
    again = h.get(f"/{kv}/okf-bundle")
    assert urlsplit(again.headers["location"]).path == url.path  # same zip, same key


def test_e5_4_rate_limit_is_429_after_20_per_second(h: Harness) -> None:
    graph_module.limiter = graph_module.RateLimiter(rate=3)
    codes = [h.get("").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_e5_4_no_write_routes_on_nodes_or_edges(h: Harness) -> None:
    kv = big(h, 2)
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        res = h.call(method, f"/projects/{h.project}/knowledge/{kv}/nodes/e00000", h.fde())
        assert res.status_code == 405


def test_e5_6_versions_carry_titles_counts_and_hash(h: Harness) -> None:
    dvid, doc = h.document("Supplier Mouser.", title="rfq-12.pdf")
    nodes, edges = graph_of(dvid, [("m", "Mouser", "Supplier")])
    kv = h.ready([doc], nodes, edges)
    with h.sessions() as s:
        row = s.get(KnowledgeVersion, kv)
        assert row is not None
        bundle = okf.build_parts(
            [doc],
            [CoverageEntry(document_version_id=dvid, status=SourceStatus.ADDED)],
            nodes,
            edges,
            b"",
        ).coverage
        row.coverage = {
            "entities": 1,
            "relations": 0,
            "chunks": 1,
            "bundle": bundle.model_dump(mode="json"),
        }
        s.commit()
    [v] = h.get("").json()["versions"]
    assert v["okf_sha256"] and v["finished_at"] and v["documents"][0]["document_id"]
    assert v["counts"] == {"documents": 1, "entities": 1, "relations": 0, "chunks": 1}
    assert v["documents"] == [
        {
            "document_version_id": str(dvid),
            "document_id": v["documents"][0]["document_id"],
            "title": "rfq-12.pdf",
            "status": "COVERED",
            "chunks": 1,
            "entities": 1,
            "reason": None,
        }
    ]


def test_e5_6_malformed_coverage_is_empty_not_an_error(h: Harness) -> None:
    kv = h.add_version(1, "FAILED")
    with h.sessions() as s:
        row = s.get(KnowledgeVersion, kv)
        assert row is not None
        row.coverage = {"documents": "not a list"}
        s.commit()
    [v] = h.get("").json()["versions"]
    assert v["documents"] == [] and v["counts"]["documents"] == 0


def test_e5_402_the_okf_bundle_is_saved_as_a_named_zip(h: Harness) -> None:
    kv = big(h, 3)
    res = h.get(f"/{kv}/okf-bundle")
    url = urlsplit(res.headers["location"])
    params = parse_qs(url.query)
    assert re.fullmatch(r"okf-bundle-v\d+-[0-9a-f]{8}\.zip", params["name"][0])
    assert params["type"] == ["application/zip"]
    download = h.client.get(f"{url.path}?{url.query}")
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["content-disposition"] == content_disposition(params["name"][0])
    assert f'filename="{params["name"][0]}"' in download.headers["content-disposition"]
