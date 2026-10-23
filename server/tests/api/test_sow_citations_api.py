"""E6.5 citation panel API: GET P/sow/versions/{v}/citations/{okf_ref_id} (+ /source), over a real
OKF bundle in storage, through the real app and auth chain."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.sow.service import Generation, record_generation
from sqlalchemy import select, update
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_knowledge_graph_api import graph_of
from test_sow_api import Harness as SowHarness

TEXT = "Acme quotes cables. Scope: quoting only. The Planner approves quotes above 5000 EUR."


class Harness(SowHarness):
    def cite(
        self, ref: str, version: int = 1, token: str | None = None, source: bool = False
    ) -> Any:
        url = f"/projects/{self.project}/sow/versions/{version}/citations/{quote(ref, safe='')}"
        return self.call("GET", url + ("/source" if source else ""), token or self.fde())

    def with_entity(self) -> tuple[uuid.UUID, str]:
        """A second knowledge version whose bundle has an entity, and SOW v2 generated from it."""
        dvid, doc = self.document(TEXT, title="RFQ cables.pdf")
        nodes, edges = graph_of(dvid, [("e-planner", "Planner", "Role")])
        kv2 = self.ready([doc], nodes, edges, number=2)
        with self.sessions() as s:
            ctx = TenantContext(TENANT_A, uuid.UUID(self.project))
            record_generation(
                s, ctx, Generation(uuid7(), kv2, self.structured, None), correlation_id="c-ent"
            )
            s.commit()
        return dvid, "e-planner"


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e6_5_t05_a_chunk_citation_gives_title_location_and_passage(h: Harness) -> None:
    res = h.cite(h.chunk)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kind"] == "chunk" and body["okf_ref_id"] == h.chunk
    assert body["title"] == "rfq.pdf" and body["source_available"] is True
    assert body["document_version_id"] == str(h.dvid)
    assert "Acme quotes cables" in body["passage"] and body["truncated"] is False


def test_e6_5_t05_source_serves_the_document_text_as_markdown(h: Harness) -> None:
    res = h.cite(h.chunk, source=True)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert 'filename="rfq.md"' in res.headers["content-disposition"]
    assert res.text == "Acme quotes cables. Scope: quoting only."


def test_e6_5_an_entity_citation_gives_its_source_passage(h: Harness) -> None:
    dvid, entity = h.with_entity()
    body = h.cite(entity, version=2).json()
    assert body["kind"] == "entity" and body["title"] == "RFQ cables.pdf"
    assert body["document_version_id"] == str(dvid)
    assert "Planner approves" in body["passage"]


def test_e6_5_a_deleted_document_shows_source_deleted(h: Harness) -> None:
    with h.sessions() as s:
        doc_id = s.scalar(select(DocumentVersion.document_id).where(DocumentVersion.id == h.dvid))
        s.execute(
            update(Document).where(Document.id == doc_id).values(deleted_at=Document.created_at)
        )
        s.commit()
    body = h.cite(h.chunk).json()
    assert body["title"] is None and body["source_available"] is False
    assert body["passage"]  # the bundle still has the text
    problem(h.cite(h.chunk, source=True), 404, "NOT_FOUND")


def test_e6_5_an_unknown_ref_or_version_is_404(h: Harness) -> None:
    problem(h.cite("c-999"), 404, "NOT_FOUND")
    problem(h.cite(h.chunk, version=9), 404, "NOT_FOUND")
    problem(h.cite("bad/ref"), 404, "NOT_FOUND")


def test_e6_5_other_tenant_and_unassigned_fde_get_404(h: Harness) -> None:
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other, h.realm.token(FDE_A2)):
        problem(h.cite(h.chunk, token=token), 404, "NOT_FOUND")
        problem(h.cite(h.chunk, token=token, source=True), 404, "NOT_FOUND")
