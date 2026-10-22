"""E6.3 SOW versions, save, diff and regenerate through the real app and auth chain, over a real
OKF bundle in storage (E6.2-T07/T08 for regenerate)."""

import copy
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.llm.models import LlmCall
from sbam_api.projects.models import Project
from sbam_api.sow.models import Sow, SowVersion
from sbam_api.sow.service import Generation, canonical_sha256, record_generation
from sbam_api.sow.tiptap import to_tiptap
from sbam_contracts.sow import SowDocument
from sbam_contracts.sow_api import MAX_TIPTAP_BYTES
from sqlalchemy import select, update
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_knowledge_graph_api import Harness as GraphHarness

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"


class Harness(GraphHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (LlmCall, Sow, SowVersion):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.dvid, doc = self.document("Acme quotes cables. Scope: quoting only.")
        self.kv = self.ready([doc], [], [])
        self.chunk = f"{self.dvid}#c0"
        raw = self.cited(json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.structured = SowDocument.model_validate(raw).model_dump(mode="json")
        self.v1 = self.generated(self.structured)
        self.set_pipeline_state("SOW_REVIEW")

    def cited(self, doc: dict[str, Any], ref: str | None = None) -> dict[str, Any]:
        cite = {
            "okf_ref_id": ref or self.chunk,
            "document_version_id": str(self.dvid),
            "location": {"line": 1},
        }

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if "citations" in node:
                    node["citations"] = [dict(cite)]
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(doc)
        return doc

    def generated(self, structured: dict[str, Any]) -> uuid.UUID:
        with self.sessions() as s:
            ctx = TenantContext(TENANT_A, uuid.UUID(self.project))
            row = record_generation(
                s, ctx, Generation(uuid7(), self.kv, structured, None), correlation_id="corr-t"
            )
            s.commit()
            return row.id

    def get(self, path: str, token: str | None = None, **params: Any) -> Any:
        return self.call("GET", f"/projects/{self.project}/sow{path}", token or self.fde())

    def etag(self) -> str:
        return str(self.get("").json()["etag"])

    def save(
        self, tiptap: Any, base: int = 1, if_match: str | None = "auto", token: str | None = None
    ) -> Any:
        headers = {}
        if if_match is not None:
            headers["If-Match"] = self.etag() if if_match == "auto" else if_match
        return self.call(
            "POST",
            f"/projects/{self.project}/sow/versions",
            token or self.fde(),
            headers=headers,
            json={"content_tiptap": tiptap, "base_version": base},
        )

    def edited(self, **change: Any) -> dict[str, Any]:
        doc = copy.deepcopy(self.structured)
        for path, value in change.items():
            node: Any = doc
            *parents, last = path.split("__")
            for part in parents:
                node = node[int(part)] if part.isdigit() else node[part]
            node[last] = value
        return to_tiptap(doc)

    def regenerate(self, body: Any = None, token: str | None = None, **headers: str) -> Any:
        return self.call(
            "POST",
            f"/projects/{self.project}/sow/regenerate",
            token or self.fde(),
            headers=headers,
            json=body,
        )

    def rows(self) -> list[SowVersion]:
        with self.sessions() as s:
            found = list(s.scalars(select(SowVersion).order_by(SowVersion.version)))
            s.expunge_all()
            return found

    def set_version_state(self, version_id: uuid.UUID, state: str) -> None:
        with self.sessions() as s:
            s.execute(update(SowVersion).where(SowVersion.id == version_id).values(state=state))
            s.commit()

    def project_state(self) -> str:
        with self.sessions() as s:
            return str(s.scalar(select(Project.state).where(Project.id == uuid.UUID(self.project))))

    def edits(self) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == "SOW_EDITED")))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


# --- reads ----------------------------------------------------------------------------------------


def test_e6_3_overview_list_and_detail(h: Harness) -> None:
    res = h.get("")
    assert res.status_code == 200
    body = res.json()
    assert body["current"]["version"] == 1 and body["approved"] is None
    assert body["etag"].startswith('"')
    [item] = h.get("/versions").json()["items"]
    assert (item["version"], item["source"], item["status"]) == (1, "GENERATED", "GENERATED")
    detail = h.get("/versions/1")
    assert detail.status_code == 200 and detail.headers["ETag"] == body["etag"]
    data = detail.json()
    assert data["structured"] == h.structured
    assert data["sha256"] == canonical_sha256(data["structured"])
    assert data["content_tiptap"]["type"] == "doc"
    assert {c["path"] for c in data["citations"]} >= {"scope", "features.0", "modules.0"}
    problem(h.get("/versions/9"), 404, "NOT_FOUND")


def test_e6_3_a_project_without_a_sow_is_404(h: Harness) -> None:
    with h.sessions() as s:
        s.execute(update(Sow).values(project_id=uuid.uuid4()))
        s.commit()
    problem(h.get(""), 404, "NOT_FOUND")


# --- save -----------------------------------------------------------------------------------------


def test_e6_3_t01_save_creates_a_new_edited_version_and_leaves_the_base_unchanged(
    h: Harness,
) -> None:
    before = h.etag()
    res = h.save(h.edited(scope__in_scope=["OTP sign-in", "KYC check", "Audit trail"]))
    assert res.status_code == 201
    body = res.json()
    assert (body["version"], body["status"], body["source"]) == (2, "IN_REVIEW", "EDITED")
    assert body["parent_version"] == 1 and body["author"]
    assert res.headers["ETag"] != before and res.headers["ETag"] == h.etag()
    v1, v2 = h.rows()
    assert v1.structured == h.structured and v1.sha256 == canonical_sha256(h.structured)
    assert v2.sha256 == canonical_sha256(v2.structured or {}) == body["sha256"]
    assert (v2.structured or {})["scope"]["in_scope"][-1] == "Audit trail"
    [audit] = h.edits()
    assert audit.after == {"version": 2, "sha256": body["sha256"]}
    assert h.get("").json()["current"]["version"] == 2


def test_e6_3_t02_concurrent_saves_from_the_same_base_second_is_409(h: Harness) -> None:
    tag = h.etag()
    first = h.save(h.edited(scope__out_of_scope=["Invoicing"]), if_match=tag)
    second = h.save(h.edited(scope__out_of_scope=["Payroll"]), if_match=tag)
    assert first.status_code == 201
    body = problem(second, 409, "SOW_VERSION_CONFLICT")
    assert body["errors"] == [{"path": "base_version", "message": "latest is 2"}]
    stale_base = h.save(h.edited(scope__out_of_scope=["Payroll"]), base=1)
    problem(stale_base, 409, "SOW_VERSION_CONFLICT")
    assert len(h.rows()) == 2


def test_e6_3_t03_missing_if_match_is_428(h: Harness) -> None:
    problem(h.save(h.edited(), if_match=None), 428, "PRECONDITION_REQUIRED")
    assert len(h.rows()) == 1


def test_e6_3_t04_removing_a_template_section_is_422_with_its_path(h: Harness) -> None:
    tiptap = h.edited()
    tiptap["content"] = [
        s for s in tiptap["content"] if s["attrs"]["section_id"] != "deliverables_acceptance"
    ]
    body = problem(h.save(tiptap), 422, "VALIDATION_ERROR")
    assert "sections.deliverables_acceptance" in [e["path"] for e in body["errors"]]
    assert len(h.rows()) == 1


def test_e6_3_renaming_a_section_heading_is_422(h: Harness) -> None:
    tiptap = h.edited()
    tiptap["content"][2]["content"][0]["content"][0]["text"] = "What we will do"
    body = problem(h.save(tiptap), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["sections.scope.heading"]


def test_e6_3_a_schema_violation_is_422_with_the_field_path(h: Harness) -> None:
    tiptap = h.edited()
    item = tiptap["content"][4]["content"][1]  # features > F-ACCESS-1
    [bound] = [n for n in item["content"] if n.get("attrs", {}).get("field") == "bound"]
    [attempts] = [n for n in bound["content"] if n["attrs"]["field"] == "max_attempts"]
    attempts["content"][0]["content"][0]["text"] = "99"
    body = problem(h.save(tiptap), 422, "VALIDATION_ERROR")
    assert "features.0.bound.max_attempts" in [e["path"] for e in body["errors"]]


def test_e6_3_an_invented_citation_is_422(h: Harness) -> None:
    doc = h.cited(copy.deepcopy(h.structured))
    doc["features"][0]["citations"][0]["okf_ref_id"] = "c-999"
    body = problem(h.save(to_tiptap(doc)), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["features.0.citations"]


def test_e6_3_t05_saving_onto_an_approved_version_is_423(h: Harness) -> None:
    h.set_version_state(h.v1, "APPROVED")
    problem(h.save(h.edited(scope__out_of_scope=["X"])), 423, "SOW_LOCKED")
    assert len(h.rows()) == 1


def test_e6_3_t09_saving_identical_content_is_a_200_no_op(h: Harness) -> None:
    tag = h.etag()
    res = h.save(h.edited())
    assert res.status_code == 200
    assert res.json()["version"] == 1 and res.headers["ETag"] == tag
    assert len(h.rows()) == 1 and h.edits() == []


def test_e6_3_t10_content_over_2_mb_is_413(h: Harness) -> None:
    tiptap = h.edited()
    size = len(json.dumps(tiptap, separators=(",", ":"), ensure_ascii=False).encode())
    pad_overhead = len(',"pad":""')
    tiptap["pad"] = "x" * (MAX_TIPTAP_BYTES + 1 - size - pad_overhead)
    assert (
        len(json.dumps(tiptap, separators=(",", ":"), ensure_ascii=False).encode())
        == MAX_TIPTAP_BYTES + 1
    )
    problem(h.save(tiptap), 413, "PAYLOAD_TOO_LARGE")
    tiptap["pad"] = tiptap["pad"][:-1]  # exactly 2 MB is accepted (and is a no-op here)
    assert h.save(tiptap).status_code == 200


# --- diff -----------------------------------------------------------------------------------------


def test_e6_3_t06_section_diff_names_the_changed_feature_only(h: Harness) -> None:
    assert h.save(h.edited(features__0__trigger="A new trigger")).status_code == 201
    res = h.get("/versions/1/diff/2")
    assert res.status_code == 200
    sections = {s["section_id"]: s for s in res.json()["sections"]}
    assert len(sections) == 9
    assert sections["features"]["change"] == "MODIFIED"
    assert sections["features"]["items"] == [{"id": "F-ACCESS-1", "change": "MODIFIED"}]
    assert {k for k, s in sections.items() if s["change"] != "UNCHANGED"} == {"features"}
    same = h.get("/versions/2/diff/2").json()
    assert {s["change"] for s in same["sections"]} == {"UNCHANGED"}
    problem(h.get("/versions/1/diff/7"), 404, "NOT_FOUND")


def test_e6_3_diff_names_added_and_removed_items(h: Harness) -> None:
    doc = copy.deepcopy(h.structured)
    doc["features"] = doc["features"][:1]
    assert h.save(to_tiptap(doc)).status_code == 201
    sections = {s["section_id"]: s for s in h.get("/versions/1/diff/2").json()["sections"]}
    assert sections["features"]["items"] == [{"id": "F-ACCESS-2", "change": "REMOVED"}]


# --- tenancy --------------------------------------------------------------------------------------


def test_e6_3_t07_other_tenant_and_unassigned_fde_get_404(h: Harness) -> None:
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other, h.realm.token(FDE_A2)):
        problem(h.get("/versions/1", token=token), 404, "NOT_FOUND")
        problem(h.get("/versions", token=token), 404, "NOT_FOUND")
        problem(h.save(h.edited(), if_match='"1"', token=token), 404, "NOT_FOUND")
        problem(h.regenerate(token=token), 404, "NOT_FOUND")
    assert len(h.rows()) == 1 and h.temporal.calls == []


# --- regenerate (E6.2-T07/T08) --------------------------------------------------------------------


def test_e6_2_t07_regenerate_in_review_starts_sow_regenerate_with_the_hint(h: Harness) -> None:
    res = h.regenerate({"hint": "  Add a QA module‮\x07 "}, **{"Idempotency-Key": "key-regen-1"})
    assert res.status_code == 202
    run = res.json()["run"]
    assert run["type"] == "SowRegenerate"
    assert run["temporal_workflow_id"] == f"sow-{h.project}"
    [call] = h.temporal.calls
    assert call["task_queue"] == "sbam-llm" and call["id_conflict_policy"].name == "FAIL"
    assert call["arg"].hint == "Add a QA module" and call["arg"].ref_id == h.kv
    [audit] = h.trail("project", "SOW_REGENERATE_STARTED")
    assert audit.after == {"run_id": run["id"], "hint_chars": 15, "resumed": False}
    again = h.regenerate({"hint": "Add a QA module"}, **{"Idempotency-Key": "key-regen-1"})
    assert again.json()["run"]["id"] == run["id"] and len(h.temporal.calls) == 1


def test_e6_2_t07_regenerate_after_a_failure_resumes_the_pipeline_at_the_sow_stage(
    h: Harness,
) -> None:
    h.set_pipeline_state("SOW_GENERATION_FAILED")
    res = h.regenerate({"hint": "Shorter"})
    assert res.status_code == 202
    assert res.json()["run"]["type"] == "ProjectPipeline"
    [call] = h.temporal.calls
    arg = call["arg"]
    assert arg.resume.stage == "sow" and arg.resume.refs == {"knowledge": h.kv, "okf": h.kv}
    assert arg.sow_hint == "Shorter"
    assert h.project_state() == "SOW_GENERATING"


def test_e6_2_t08_regenerating_an_approved_sow_is_409(h: Harness) -> None:
    h.set_version_state(h.v1, "APPROVED")
    problem(h.regenerate(), 409, "SOW_APPROVED")
    assert h.temporal.calls == []


@pytest.mark.parametrize(
    ("state", "code"),
    [("SOW_GENERATING", "SOW_GENERATING"), ("KNOWLEDGE_BUILDING", "NOT_IN_REVIEW")],
)
def test_e6_3_regenerate_in_the_wrong_state_is_409(h: Harness, state: str, code: str) -> None:
    h.set_pipeline_state(state)
    problem(h.regenerate(), 409, code)
    assert h.temporal.calls == []


def test_e6_3_regenerate_hint_over_2000_characters_is_422(h: Harness) -> None:
    problem(h.regenerate({"hint": "x" * 2001}), 422, "VALIDATION_ERROR")
    assert h.temporal.calls == []


def test_e6_3_regenerate_while_one_runs_is_409(h: Harness) -> None:
    assert h.regenerate().status_code == 202
    problem(h.regenerate(), 409, "SOW_GENERATING")  # sow-<project> is still running
