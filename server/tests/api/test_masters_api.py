"""E3.2 masters engine through the real app and auth chain (#48), on SQLite.

The projects harness (test_projects) gives tenants A/B, admin tokens and project creation; the
masters tables are added to its database, with the SQLite version of the append-only guard.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import test_projects
from sbam_api.audit.models import AuditLog
from sbam_api.core.auth import CurrentUser
from sbam_api.masters import service
from sbam_api.masters.models import MasterEntry, MasterEntryVersion, MasterSnapshot
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from test_auth import ADMIN_A, TENANT_A, problem
from test_projects import Harness

RULE_BOM = {
    "domain_rule": "RULE-BOM: applies to BOM. Policy: compare source and costed BOM",
    "misc": "Status: Proposed",
}


_projects_engine = test_projects.sqlite_engine  # captured before the fixture patches it


def engine_with_masters() -> Engine:
    engine = _projects_engine()
    t = MasterEntry.metadata.tables
    MasterEntry.metadata.create_all(
        engine,
        tables=[
            t["sbam.master_entries"],
            t["sbam.master_entry_versions"],
            t["sbam.master_snapshots"],
            t["sbam.master_documents"],  # a snapshot also pins the parsed master documents
            t["sbam.filetype_row_versions"],  # the platform rows every import compares to
        ],
    )
    return engine


class MastersHarness(Harness):
    def post(self, master_type: str, body: dict[str, Any], token: str | None = None) -> Any:
        return self.call(
            "POST", f"/admin/masters/{master_type}", token or self.admin_a(), json=body
        )

    def entry(self, master_type: str = "RULES", **body: Any) -> dict[str, Any]:
        res = self.post(master_type, {"payload": RULE_BOM, **body})
        assert res.status_code == 201, res.text
        out: dict[str, Any] = res.json()
        return out

    def version(self, entry: dict[str, Any], payload: dict[str, Any], etag: str) -> Any:
        path = f"/admin/masters/{entry['master_type']}/{entry['id']}/versions"
        return self.call(
            "POST", path, self.admin_a(), json={"payload": payload}, headers={"if-match": etag}
        )

    def user(self) -> CurrentUser:
        return CurrentUser(
            user_id=ADMIN_A, keycloak_sub="admin-a", tenant_id=TENANT_A, roles=frozenset()
        )


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MastersHarness]:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_masters)
    yield MastersHarness(tmp_path)


# --- T01 / T02: create and validate ---------------------------------------------------------------


def test_e3_2_t01_create_rules_entry(h: MastersHarness) -> None:
    res = h.post("RULES", {"payload": RULE_BOM})
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["stable_id"], body["status"], body["version"]) == ("RULE-BOM", "PROPOSED", 1)
    assert body["source_kind"] == "TEMPLATE_ROW" and body["row_label"] == "Rule #2"
    assert body["payload"]["domain_rule"].startswith("RULE-BOM:")
    assert "stable_id" not in body["payload"] and "status" not in body["payload"]
    assert res.headers["etag"] == '"1"'
    with h.sessions() as s:
        audit = s.scalars(select(AuditLog).where(AuditLog.entity_id == body["id"])).one()
    assert audit.action == "MASTER_ENTRY_CREATED"
    assert audit.after is not None and audit.after["version"] == 1 and audit.after["version_id"]


def test_e3_2_t02_extra_payload_key_is_422_on_its_path(h: MastersHarness) -> None:
    res = h.post("RULES", {"payload": {**RULE_BOM, "priority": "high"}})
    body = problem(res, 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["payload.priority"]


def test_e3_2_sbam_metadata_never_goes_into_the_payload(h: MastersHarness) -> None:
    res = h.post("RULES", {"payload": {**RULE_BOM, "stable_id": "X"}})
    assert [e["path"] for e in problem(res, 422, "VALIDATION_ERROR")["errors"]] == [
        "payload.stable_id"
    ]
    problem(h.post("RULES", {"payload": RULE_BOM, "status": "APPROVED"}), 422, "VALIDATION_ERROR")


def test_e3_2_filetype_is_read_only_for_admins(h: MastersHarness) -> None:
    payload = {
        "file_format": ".x",
        "tier": 1,
        "purpose": "p",
        "rag_ingestion": "r",
        "example": "https://e.x",
    }
    problem(h.post("FILETYPE", {"payload": payload}), 403, "FILETYPE_FROZEN")  # E3.7


def test_e3_2_labels_count_up_per_type(h: MastersHarness) -> None:
    h.entry()
    second = h.entry(payload={"domain_rule": "RULE-OFFER: offers"})
    assert second["row_label"] == "Rule #3"
    agent = h.entry("AGENTS", payload={"agent_name": "Intake", "description": "AG-INTAKE. x"})
    assert (agent["row_label"], agent["stable_id"]) == ("Agent #2", "AG-INTAKE")


def test_e3_2_duplicate_stable_id_in_the_same_scope_is_409(h: MastersHarness) -> None:
    h.entry()
    problem(h.post("RULES", {"payload": RULE_BOM}), 409, "CONFLICT")


def test_e3_2_tooling_entry_is_labelled_with_its_id(h: MastersHarness) -> None:
    tool = {"category": "MCP", "examples": "WebMCP", "record_status": "Category recorded"}
    body = h.entry("TOOLING", payload=tool, row_label="TOOL-MFG-12")
    assert (body["stable_id"], body["status"], body["source_kind"]) == (
        "TOOL-MFG-12",
        "PROPOSED",
        "TEMPLATE_ROW",
    )
    res = h.post("TOOLING", {"payload": tool})  # no id
    assert [e["path"] for e in problem(res, 422, "VALIDATION_ERROR")["errors"]] == ["row_label"]


# --- versions, T06 --------------------------------------------------------------------------------


def test_e3_2_new_version_is_n_plus_one_and_old_versions_stay(h: MastersHarness) -> None:
    entry = h.entry()
    approved = {**RULE_BOM, "misc": "Reviewed. Status: Approved"}
    res = h.version(entry, approved, '"1"')
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["version"], body["status"]) == (2, "APPROVED")
    assert res.headers["etag"] == '"2"'
    with h.sessions() as s:
        versions = s.scalars(select(MasterEntryVersion).order_by(MasterEntryVersion.version)).all()
    assert [(v.version, v.payload["misc"]) for v in versions] == [
        (1, "Status: Proposed"),
        (2, "Reviewed. Status: Approved"),
    ]
    got = h.call("GET", f"/admin/masters/RULES/{entry['id']}", h.admin_a()).json()
    assert got["version"] == 2


def test_e3_2_t06_second_version_on_the_same_etag_is_409_version_conflict(
    h: MastersHarness,
) -> None:
    entry = h.entry()
    assert h.version(entry, {**RULE_BOM, "misc": "a"}, '"1"').status_code == 201
    problem(h.version(entry, {**RULE_BOM, "misc": "b"}, '"1"'), 409, "VERSION_CONFLICT")


def test_e3_2_t06_losing_the_race_on_version_n_plus_one_is_409(h: MastersHarness) -> None:
    """Both writers passed the ETag check; the unique (entry, version) decides."""
    entry = h.entry()
    with h.sessions() as s:  # a concurrent writer already stored version 2
        s.add(
            MasterEntryVersion(
                tenant_id=TENANT_A, entry_id=uuid.UUID(entry["id"]), version=2, payload=RULE_BOM
            )
        )
        s.commit()
    problem(h.version(entry, {**RULE_BOM, "misc": "late"}, '"1"'), 409, "VERSION_CONFLICT")


def test_e3_2_version_needs_if_match(h: MastersHarness) -> None:
    entry = h.entry()
    path = f"/admin/masters/RULES/{entry['id']}/versions"
    res = h.call("POST", path, h.admin_a(), json={"payload": RULE_BOM})
    problem(res, 428, "PRECONDITION_REQUIRED")


def test_e3_2_stable_id_cannot_change_in_a_version(h: MastersHarness) -> None:
    entry = h.entry()
    res = h.version(entry, {"domain_rule": "RULE-OFFER: other"}, '"1"')
    problem(res, 422, "VALIDATION_ERROR")


# --- T03: versions are append-only ----------------------------------------------------------------


def test_e3_2_t03_updating_a_version_is_rejected(h: MastersHarness) -> None:
    entry = h.entry()
    with h.sessions() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("UPDATE master_entry_versions SET payload = '{}'"))
        s.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("DELETE FROM master_entry_versions"))
        s.rollback()
        row = s.scalars(select(MasterEntryVersion)).one()
    assert str(row.entry_id) == entry["id"]
    assert {k: row.payload[k] for k in RULE_BOM} == RULE_BOM  # unchanged


# --- T08 and tenant isolation ---------------------------------------------------------------------


def test_e3_2_t08_project_of_another_tenant_is_404(h: MastersHarness) -> None:
    other = h.call("POST", "/admin/projects", h.admin_b(), json={"name": "B project"}).json()
    problem(h.post("RULES", {"payload": RULE_BOM, "project_id": other["id"]}), 404, "NOT_FOUND")
    with h.sessions() as s:
        assert s.scalars(select(MasterEntry)).all() == []


def test_e3_2_entries_of_another_tenant_are_404(h: MastersHarness) -> None:
    entry = h.entry()
    problem(h.call("GET", f"/admin/masters/RULES/{entry['id']}", h.admin_b()), 404, "NOT_FOUND")
    assert h.call("GET", "/admin/masters/RULES", h.admin_b()).json()["items"] == []


def test_e3_2_archived_project_takes_no_entries(h: MastersHarness) -> None:
    project = h.create("Old")
    h.call("POST", f"/admin/projects/{project['id']}/archive", h.admin_a())
    res = h.post("RULES", {"payload": RULE_BOM, "project_id": project["id"]})
    problem(res, 409, "PROJECT_ARCHIVED")


def test_e3_2_fde_cannot_reach_the_masters_api(h: MastersHarness) -> None:
    res = h.call("GET", "/admin/masters/RULES", h.realm.token())
    assert res.status_code in (403, 404)


# --- snapshots, T05 / T07 -------------------------------------------------------------------------


def test_e3_2_t05_snapshot_pins_the_project_entry_over_the_tenant_entry(h: MastersHarness) -> None:
    project = h.create("EMS RFQ costing")
    tenant = h.entry()
    h.version(tenant, {**RULE_BOM, "misc": "v2"}, '"1"')  # tenant RULE-BOM v2
    own = h.entry(project_id=project["id"])  # project-bound RULE-BOM v1
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), uuid.UUID(project["id"]))
    assert [(e["entry_id"], e["version"]) for e in snap.entries] == [(own["id"], 1)]
    got = h.call("GET", f"/admin/masters/snapshots/{snap.id}", h.admin_a())
    assert got.status_code == 200, got.text
    assert got.json()["sha256"] == snap.sha256
    problem(h.call("GET", f"/admin/masters/snapshots/{snap.id}", h.admin_b()), 404, "NOT_FOUND")


def test_e3_2_snapshot_for_a_project_without_entries_is_empty_but_hashed(
    h: MastersHarness,
) -> None:
    project = h.create("Empty")
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), uuid.UUID(project["id"]))
    assert snap.entries == [] and snap.sha256.startswith("sha256:")


def test_e3_2_t04_two_snapshots_of_the_same_versions_hash_the_same(h: MastersHarness) -> None:
    project = h.create("Twice")
    h.entry()
    with h.sessions() as s:
        one = service.create_snapshot(s, h.user(), uuid.UUID(project["id"]))
        two = service.create_snapshot(s, h.user(), uuid.UUID(project["id"]))
    assert one.id != two.id and one.sha256 == two.sha256 and one.entries == two.entries


def test_e3_2_t07_new_version_marks_the_context_outdated_without_starting_anything(
    h: MastersHarness,
) -> None:
    project = h.create("Knowledge")
    skill = h.entry("SKILLS", payload={"skill": "SK-INTAKE: RFQ intake"})
    with h.sessions() as s:
        snap = service.create_snapshot(s, h.user(), uuid.UUID(project["id"]))
        assert service.context_outdated(s, h.user(), snap.id) is False
    with mock.patch("sbam_api.temporal.service.start_run") as start_run:
        h.version(skill, {"skill": "SK-INTAKE: RFQ intake, revision 2"}, '"1"')
        with h.sessions() as s:
            assert service.context_outdated(s, h.user(), snap.id) is True
    start_run.assert_not_called()


def test_e3_2_snapshots_are_append_only(h: MastersHarness) -> None:
    project = h.create("Pinned")
    with h.sessions() as s:
        service.create_snapshot(s, h.user(), uuid.UUID(project["id"]))
        with pytest.raises(DBAPIError, match="append-only"):
            s.execute(text("UPDATE master_snapshots SET sha256 = 'x'"))
        s.rollback()
        assert len(s.scalars(select(MasterSnapshot)).all()) == 1
