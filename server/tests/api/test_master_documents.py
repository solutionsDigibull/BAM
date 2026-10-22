"""E3.8 master documents through the real app and auth chain, on SQLite, with a fake clamd.

Same stand-ins as the masters import tests: SQLite with schema `sbam` mapped away, a temp-dir
storage root and a fake clamd on a real socket. Parsing and Cognee are a later issue, so every
document here stays `parse_status = PENDING`.
"""

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

import pytest
import test_projects
from clamd_fake import FakeClamd
from fixtures import eicar_bytes, fixture_path
from sbam_api.audit.models import AuditLog
from sbam_api.masters import documents
from sbam_api.masters.models import MasterDocument
from sbam_api.projects.models import Project
from sbam_api.scan.clamd import ClamdClient, ScanVerdict
from sqlalchemy import Engine, select
from test_auth import TENANT_A, problem
from test_masters_api import MastersHarness, engine_with_masters

XLSX = fixture_path("documents", "multi_sheet.xlsx").read_bytes()
OPENAPI = b"openapi: 3.0.3\ninfo:\n  title: Rates\n  version: '1'\npaths: {}\n"
SWAGGER = b'{"swagger": "2.0", "info": {"title": "Rates", "version": "1"}, "paths": {}}'
POLICY = b"approved_currencies:\n  - EUR\n  - USD\n"
FASTMCP = b"from fastmcp import FastMCP\n\nmcp = FastMCP('rates')\n"
ZERO = uuid.UUID(int=0)


def engine_with_documents() -> Engine:
    engine = engine_with_masters()
    MasterDocument.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
    return engine


class DocsHarness(MastersHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.tmp = tmp_path

    def attach(
        self,
        name: str,
        data: bytes,
        token: str | None = None,
        **form: str,
    ) -> Any:
        return self.call(
            "POST",
            "/admin/master-documents",
            token or self.admin_a(),
            files={"file": (name, data, "application/octet-stream")},
            data=form,
        )

    def patch(
        self, doc_id: str, body: dict[str, Any], etag: str | None, token: str | None = None
    ) -> Any:
        headers = {"if-match": etag} if etag else {}
        return self.call(
            "PATCH",
            f"/admin/master-documents/{doc_id}",
            token or self.admin_a(),
            json=body,
            headers=headers,
        )

    def etag(self, doc_id: str) -> str:
        res = self.call("GET", f"/admin/master-documents/{doc_id}", self.admin_a())
        assert res.status_code == 200, res.text
        return str(res.headers["etag"])

    def rows(self) -> list[MasterDocument]:
        with self.sessions() as s:
            return list(s.scalars(select(MasterDocument)))

    def actions(self, action: str) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == action)))

    def stored(self, kind: str, data: bytes, project: uuid.UUID = ZERO) -> Path:
        return self.tmp / f"{TENANT_A}/{project}/{kind}/{hashlib.sha256(data).hexdigest()}"


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    with FakeClamd() as fake:
        yield fake


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clamd: FakeClamd) -> DocsHarness:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_documents)
    monkeypatch.setattr(documents, "SCAN_BACKOFF", 0.0)
    harness = DocsHarness(tmp_path)
    state = harness.client.app.state  # type: ignore[attr-defined]
    state.settings = state.settings.model_copy(
        update={"clamav_host": clamd.host, "clamav_port": clamd.port, "clamav_timeout": 5.0}
    )
    return harness


# --- routing by Filetype ----------------------------------------------------------------------


def test_e3_8_t01_a_workbook_routes_to_skills_as_source_data(h: DocsHarness) -> None:
    res = h.attach("rates.xlsx", XLSX)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["master_type"] == "SKILLS" and body["route_source"] == "DEFAULT_BY_FILETYPE"
    assert (
        body["intent_class"].startswith("Source data")
        and body["filetype_row_label"] == "Filetype #23"
    )
    assert (body["scan_status"], body["parse_status"]) == ("CLEAN", "PENDING")
    assert body["title"] == "rates.xlsx" and body["sha256"] == hashlib.sha256(XLSX).hexdigest()
    assert body["project_id"] is None and res.headers["etag"] == '"1"'
    assert h.stored("uploads", XLSX).read_bytes() == XLSX  # through the storage backend
    assert [a.action for a in h.actions("MASTER_DOCUMENT_ATTACHED")] == ["MASTER_DOCUMENT_ATTACHED"]


def test_e3_8_t02_an_openapi_file_routes_to_agents(h: DocsHarness) -> None:
    body = h.attach("openapi.yaml", OPENAPI).json()
    assert body["master_type"] == "AGENTS" and body["filetype_row_label"] == "Filetype #15"
    assert body["intent_class"] == "Agent / tool definition"
    assert body["route_source"] == "DEFAULT_BY_FILETYPE"


@pytest.mark.parametrize(
    ("name", "data", "master", "row"),
    [
        ("api.json", SWAGGER, "AGENTS", "Filetype #16"),
        ("server.py", FASTMCP, "AGENTS", "Filetype #13"),
        ("policy.yaml", POLICY, "RULES", "Filetype #12"),
        ("notes.md", b"# Notes\n", "SKILLS", "Filetype #6"),
        ("tool.py", b"print('hi')\n", "AGENTS", "Filetype #8"),
        ("cert.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "ORGANIZATION", "Filetype #35"),
    ],
    ids=["swagger", "fastmcp", "yaml-policy", "markdown", "python", "png-evidence"],
)
def test_e3_8_the_route_follows_the_filetype_row(
    h: DocsHarness, name: str, data: bytes, master: str, row: str
) -> None:
    body = h.attach(name, data).json()
    assert (body["master_type"], body["filetype_row_label"]) == (master, row)


def test_e3_8_the_admin_can_choose_the_master_at_upload(h: DocsHarness) -> None:
    body = h.attach("policy.yaml", POLICY, master_type="SKILLS").json()
    assert (body["master_type"], body["route_source"]) == ("SKILLS", "ADMIN_OVERRIDE")
    same = h.attach("policy2.yaml", b"x: 1\n", master_type="RULES").json()
    assert (same["master_type"], same["route_source"]) == ("RULES", "DEFAULT_BY_FILETYPE")


@pytest.mark.parametrize("master_type", ["TOOLING", "FILETYPE", "nope"])
def test_e3_8_a_master_that_takes_no_documents_is_a_validation_error(
    h: DocsHarness, master_type: str
) -> None:
    res = h.attach("a.txt", b"hello\n", master_type=master_type)
    assert res.status_code == 422 and not h.rows()


# --- override ---------------------------------------------------------------------------------


def test_e3_8_t03_patching_the_master_is_an_audited_admin_override(h: DocsHarness) -> None:
    doc = h.attach("rates.xlsx", XLSX).json()
    res = h.patch(doc["id"], {"master_type": "RULES"}, h.etag(doc["id"]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["master_type"], body["route_source"]) == ("RULES", "ADMIN_OVERRIDE")
    assert res.headers["etag"] == '"2"'
    [audit] = h.actions("MASTER_DOCUMENT_ROUTED")
    assert audit.before == {"master_type": "SKILLS", "master_entry_id": None}
    assert audit.after == {"master_type": "RULES", "master_entry_id": None}
    assert (
        h.call("GET", "/admin/master-documents", h.admin_a()).json()["items"][0]["master_type"]
        == "RULES"
    )


def test_e3_8_a_patch_needs_a_current_if_match(h: DocsHarness) -> None:
    doc = h.attach("rates.xlsx", XLSX).json()
    assert h.patch(doc["id"], {"master_type": "RULES"}, None).status_code == 428
    assert h.patch(doc["id"], {"master_type": "RULES"}, '"7"').status_code == 412
    assert h.rows()[0].master_type == "SKILLS"


def test_e3_8_patching_the_route_it_already_has_writes_nothing(h: DocsHarness) -> None:
    doc = h.attach("rates.xlsx", XLSX).json()
    res = h.patch(doc["id"], {"master_type": "SKILLS"}, h.etag(doc["id"]))
    assert res.status_code == 200 and res.json()["route_source"] == "DEFAULT_BY_FILETYPE"
    assert h.actions("MASTER_DOCUMENT_ROUTED") == [] and h.rows()[0].row_version == 1


def test_e3_8_an_entry_must_belong_to_the_chosen_master(h: DocsHarness) -> None:
    doc = h.attach("rates.xlsx", XLSX).json()
    rule = h.entry("RULES")["id"]
    etag = h.etag(doc["id"])
    wrong = h.patch(doc["id"], {"master_type": "SKILLS", "master_entry_id": rule}, etag)
    assert wrong.status_code == 422 and wrong.json()["errors"][0]["path"] == "master_entry_id"
    unknown = h.patch(
        doc["id"], {"master_type": "RULES", "master_entry_id": str(uuid.uuid4())}, etag
    )
    assert unknown.status_code == 422
    ok = h.patch(doc["id"], {"master_type": "RULES", "master_entry_id": rule}, etag)
    assert ok.status_code == 200 and ok.json()["master_entry_id"] == rule


def test_e3_8_an_entry_of_another_tenant_is_not_an_entry(h: DocsHarness) -> None:
    doc = h.attach("rates.xlsx", XLSX).json()
    other = h.call(
        "POST",
        "/admin/masters/RULES",
        h.admin_b(),
        json={"payload": {"domain_rule": "RULE-BOM: applies to BOM. Policy: x"}},
    ).json()["id"]
    res = h.patch(doc["id"], {"master_type": "RULES", "master_entry_id": other}, h.etag(doc["id"]))
    assert res.status_code == 422


# --- the checks before and during the scan ----------------------------------------------------


def test_e3_8_t04_an_infected_file_is_quarantined_and_never_stored_as_a_document(
    h: DocsHarness, clamd: FakeClamd
) -> None:
    res = h.attach("eicar.txt", eicar_bytes())
    problem(res, 422, "MALWARE")
    assert h.rows() == [] and clamd.scans == 1
    assert h.stored("quarantine", eicar_bytes()).read_bytes() == eicar_bytes()
    assert not h.stored("uploads", eicar_bytes()).exists()
    [audit] = h.actions("MASTER_DOCUMENT_QUARANTINED")
    assert audit.after is not None and audit.after["signature"]
    assert h.call("GET", "/admin/master-documents", h.admin_a()).json() == {"items": []}


def test_e3_8_t05_a_file_over_15_mb_is_too_large(h: DocsHarness, clamd: FakeClamd) -> None:
    res = h.attach("big.txt", b"a" * (15 * 1024 * 1024 + 1))
    problem(res, 413, "FILE_TOO_LARGE")
    assert clamd.scans == 0 and h.rows() == []


def test_e3_8_exactly_15_mb_is_accepted(h: DocsHarness) -> None:
    assert h.attach("max.txt", b"a" * (15 * 1024 * 1024)).status_code == 201


def test_e3_8_t06_a_future_type_is_unsupported(h: DocsHarness, clamd: FakeClamd) -> None:
    res = h.attach("part.dwg", b"AC1027\x00" + b"\x00" * 32)
    problem(res, 415, "UNSUPPORTED_TYPE")
    assert clamd.scans == 0 and h.rows() == []


@pytest.mark.parametrize("name", ["noext", "x.exe", "page.html", "a.parquet"])
def test_e3_8_an_extension_without_an_enabled_row_is_unsupported(h: DocsHarness, name: str) -> None:
    assert h.attach(name, b"hello\n").status_code == 415


def test_e3_8_an_empty_file_is_a_validation_error(h: DocsHarness) -> None:
    assert h.attach("empty.txt", b"").status_code == 422


def test_e3_8_a_path_in_the_file_name_is_dropped(h: DocsHarness) -> None:
    assert h.attach("..\\..\\etc/notes.txt", b"hi\n").json()["title"] == "notes.txt"


def test_e3_8_t07_no_verdict_after_the_last_attempt_is_scan_failed_and_never_routable(
    h: DocsHarness, clamd: FakeClamd, monkeypatch: pytest.MonkeyPatch
) -> None:
    tries: list[int] = []
    scan = ClamdClient.scan

    def counting(self: ClamdClient, stream: BinaryIO) -> ScanVerdict:
        tries.append(1)
        return scan(self, stream)

    monkeypatch.setattr(ClamdClient, "scan", counting)
    clamd.behaviour = "drop"
    res = h.attach("rates.xlsx", XLSX)
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["scan_status"], body["parse_status"]) == ("SCAN_FAILED", "PENDING")
    assert len(tries) == documents.SCAN_ATTEMPTS  # five tries, then no pass by default
    patch = h.patch(body["id"], {"master_type": "RULES"}, h.etag(body["id"]))
    assert patch.status_code == 409  # not routable until a clean verdict

    clamd.behaviour = "normal"  # clamd is back: the same file scans again, in place
    again = h.attach("rates.xlsx", XLSX)
    assert again.status_code == 200 and again.json()["id"] == body["id"]
    assert again.json()["scan_status"] == "CLEAN" and len(h.rows()) == 1
    assert h.patch(body["id"], {"master_type": "RULES"}, h.etag(body["id"])).status_code == 200


def test_e3_8_an_infected_re_upload_of_an_unscanned_file_removes_it(
    h: DocsHarness, clamd: FakeClamd
) -> None:
    clamd.behaviour = "drop"
    assert h.attach("eicar.txt", eicar_bytes()).json()["scan_status"] == "SCAN_FAILED"
    clamd.behaviour = "normal"
    assert h.attach("eicar.txt", eicar_bytes()).status_code == 422
    assert h.call("GET", "/admin/master-documents", h.admin_a()).json()["items"] == []


# --- dedupe and scope -------------------------------------------------------------------------


def test_e3_8_the_same_file_twice_is_linked_not_duplicated(
    h: DocsHarness, clamd: FakeClamd
) -> None:
    first = h.attach("rates.xlsx", XLSX)
    second = h.attach("copy-of-rates.xlsx", XLSX)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["id"] == first.json()["id"] and second.json()["title"] == "rates.xlsx"
    assert len(h.rows()) == 1 and clamd.scans == 1  # linked before the scan: nothing to scan again


def test_e3_8_the_same_file_in_a_project_and_in_the_tenant_is_two_documents(h: DocsHarness) -> None:
    project = h.create("Quote Bot")["id"]
    tenant_wide = h.attach("rates.xlsx", XLSX).json()
    in_project = h.attach("rates.xlsx", XLSX, project_id=project)
    assert in_project.status_code == 201
    assert (
        in_project.json()["id"] != tenant_wide["id"] and in_project.json()["project_id"] == project
    )
    assert h.stored("uploads", XLSX, uuid.UUID(project)).exists()
    assert len(h.rows()) == 2


def test_e3_8_the_same_file_in_two_tenants_is_two_documents(h: DocsHarness) -> None:
    a = h.attach("rates.xlsx", XLSX)
    b = h.attach("rates.xlsx", XLSX, token=h.admin_b())
    assert (a.status_code, b.status_code) == (201, 201) and a.json()["id"] != b.json()["id"]


# --- projects ---------------------------------------------------------------------------------


def test_e3_8_a_project_of_another_tenant_is_not_found(h: DocsHarness) -> None:
    mine = h.create("Mine")["id"]
    assert h.attach("a.txt", b"hi\n", token=h.admin_b(), project_id=mine).status_code == 404


def test_e3_8_an_archived_project_takes_no_documents(h: DocsHarness) -> None:
    project = h.create("Old")["id"]
    with h.sessions() as s:
        row = s.get(Project, uuid.UUID(project))
        assert row is not None
        row.status = "ARCHIVED"
        s.commit()
    res = h.attach("a.txt", b"hi\n", project_id=project)
    problem(res, 409, "PROJECT_ARCHIVED")


def test_e3_8_the_list_filters_by_master_and_project(h: DocsHarness) -> None:
    project = h.create("Quote Bot")["id"]
    h.attach("rates.xlsx", XLSX)
    h.attach("openapi.yaml", OPENAPI, project_id=project)
    h.attach("notes.md", b"# n\n", project_id=project)

    def names(**params: str) -> list[str]:
        res = h.call("GET", "/admin/master-documents", h.admin_a(), params=params)
        assert res.status_code == 200
        return sorted(i["title"] for i in res.json()["items"])

    assert names() == ["notes.md", "openapi.yaml", "rates.xlsx"]
    assert names(master_type="AGENTS") == ["openapi.yaml"]
    assert names(project_id=project) == ["notes.md", "openapi.yaml"]
    assert names(master_type="SKILLS", project_id=project) == ["notes.md"]


# --- isolation and roles ----------------------------------------------------------------------


def test_e3_8_t08_another_tenants_document_is_not_found(h: DocsHarness) -> None:
    doc = h.attach("rates.xlsx", XLSX).json()
    etag = h.etag(doc["id"])
    res = h.patch(doc["id"], {"master_type": "RULES"}, etag, token=h.admin_b())
    assert res.status_code == 404
    assert h.call("GET", f"/admin/master-documents/{doc['id']}", h.admin_b()).status_code == 404
    assert h.call("GET", "/admin/master-documents", h.admin_b()).json() == {"items": []}
    assert h.rows()[0].master_type == "SKILLS"


def test_e3_8_a_missing_document_is_not_found(h: DocsHarness) -> None:
    missing = str(uuid.uuid4())
    assert h.call("GET", f"/admin/master-documents/{missing}", h.admin_a()).status_code == 404
    assert h.patch(missing, {"master_type": "RULES"}, '"1"').status_code == 404


def test_e3_8_a_file_is_a_required_part(h: DocsHarness) -> None:
    res = h.call("POST", "/admin/master-documents", h.admin_a(), data={"master_type": "RULES"})
    assert res.status_code == 422
