"""MUI-3 (#782): bulk master rows, server-assigned ids and retire-as-delete, via the real app."""

from pathlib import Path
from typing import Any

import pytest
import test_projects
from sbam_api.masters.models import MasterEntry, MasterEntryVersion
from sqlalchemy import select
from test_auth import problem
from test_masters_api import RULE_BOM, MastersHarness, engine_with_masters

SKILL = {"skill_id": "SK-QUOTE", "skill": "Quote", "instruction": "Price the BOM"}


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MastersHarness:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_masters)
    return MastersHarness(tmp_path)


def bulk(h: MastersHarness, master_type: str, rows: list[dict[str, str]], **kw: Any) -> Any:
    mode = kw.pop("mode", "upsert")
    path = f"/admin/masters/{master_type}/bulk"
    if "project_id" in kw:
        path += f"?project_id={kw.pop('project_id')}"
    token = kw.pop("token", None) or h.admin_a()
    return h.call("POST", path, token, json={"mode": mode, "rows": rows})


def report(res: Any) -> dict[str, Any]:
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


def stable_ids(h: MastersHarness) -> list[str]:
    with h.sessions() as s:
        return sorted(s.scalars(select(MasterEntry.stable_id)))


def retire(h: MastersHarness, entry: dict[str, Any], etag: str | None, token: str = "") -> Any:
    headers = {"if-match": etag} if etag is not None else {}
    path = f"/admin/masters/{entry['master_type']}/{entry['id']}/retire"
    return h.call("POST", path, token or h.admin_a(), headers=headers)


# --- bulk -----------------------------------------------------------------------------------------


def test_mui_3_t01_validate_only_writes_nothing(h: MastersHarness) -> None:
    out = report(bulk(h, "SKILLS", [SKILL, {"skill": "blank id"}], mode="validate-only"))
    assert (out["applied"], out["created"]) == (False, ["SK-QUOTE", "SK-01"])
    assert stable_ids(h) == []


def test_mui_3_t02_upsert_creates_rows_and_audits_each(h: MastersHarness) -> None:
    out = report(bulk(h, "SKILLS", [SKILL, {**SKILL, "skill_id": "SK-RFQ"}]))
    assert (out["applied"], out["created"], out["errors"]) == (True, ["SK-QUOTE", "SK-RFQ"], [])
    assert stable_ids(h) == ["SK-QUOTE", "SK-RFQ"]
    with h.sessions() as s:
        entry = s.scalars(select(MasterEntry).where(MasterEntry.stable_id == "SK-RFQ")).one()
    assert h.audit_actions(str(entry.id)) == ["MASTER_ENTRY_CREATED"]
    assert entry.row_label == "SK-RFQ"


def test_mui_3_t03_an_exact_repeat_is_skipped(h: MastersHarness) -> None:
    report(bulk(h, "SKILLS", [SKILL]))
    out = report(bulk(h, "SKILLS", [{**SKILL, "skill_id": "sk-quote"}]))  # the id, case aside
    assert (out["skipped_exact"], out["created"], out["conflicts"]) == (1, [], [])


def test_mui_3_t04_a_changed_existing_id_is_a_conflict_and_not_overwritten(
    h: MastersHarness,
) -> None:
    report(bulk(h, "SKILLS", [SKILL]))
    out = report(bulk(h, "SKILLS", [{**SKILL, "skill": "Quote v2", "outcome": "a quote"}]))
    assert out["conflicts"] == [{"row": 1, "stable_id": "SK-QUOTE", "fields": ["outcome", "skill"]}]
    with h.sessions() as s:
        versions = s.scalars(select(MasterEntryVersion)).all()
    assert len(versions) == 1 and versions[0].payload["skill"] == "Quote"


def test_mui_3_t05_a_row_error_carries_its_row_number_and_clean_rows_still_land(
    h: MastersHarness,
) -> None:
    rows = [SKILL, {"skill_id": "SK-X", "priority": "high"}, {"skill_id": "two\nlines"}]
    out = report(bulk(h, "SKILLS", rows))
    assert [(e["row"], e["field"], e["code"]) for e in out["errors"]] == [
        (2, "priority", "VALIDATION_ERROR"),
        (3, "skill_id", "INVALID_ID"),
    ]
    assert out["created"] == ["SK-QUOTE"] and stable_ids(h) == ["SK-QUOTE"]


def test_mui_3_t06_duplicate_ids_in_one_request(h: MastersHarness) -> None:
    out = report(bulk(h, "SKILLS", [SKILL, {**SKILL, "skill_id": "sk-quote "}]))
    assert [(e["row"], e["code"]) for e in out["errors"]] == [(2, "DUPLICATE_ID")]
    assert stable_ids(h) == ["SK-QUOTE"]


def test_mui_3_t07_blank_ids_are_assigned_in_sequence_per_prefix(h: MastersHarness) -> None:
    out = report(bulk(h, "SKILLS", [{"skill": "a"}, {"skill": "b"}, {"skill_id": "SK-07"}]))
    assert out["created"] == ["SK-08", "SK-09", "SK-07"]  # past the request's own SK-07
    out = report(bulk(h, "SKILLS", [{"skill": "c"}]))
    assert out["created"] == ["SK-10"]
    agents = [{"agent_name": "a"}, {"agent_name": "b", "kind": "Assistant"}, {"agent_name": "c"}]
    assert report(bulk(h, "AGENTS", agents))["created"] == ["AG-01", "AS-01", "AG-02"]
    rules = [{"rule_type_description": "x"}, {"rule_type_description": "y"}]
    assert report(bulk(h, "RULES", rules))["created"] == ["RULE-CUSTOM-001", "RULE-CUSTOM-002"]
    assert report(bulk(h, "ORGANIZATION", [{"company_type": "HQ"}]))["created"] == ["ORG-01"]
    assert report(bulk(h, "TOOLING", [{"tool_name": "git"}]))["created"] == ["TOOL-01"]
    with h.sessions() as s:
        row = s.scalars(select(MasterEntry).where(MasterEntry.stable_id == "SK-08")).one()
        version = s.scalars(
            select(MasterEntryVersion).where(MasterEntryVersion.entry_id == row.id)
        ).one()
    assert row.row_label == "SK-08" and version.payload["skill_id"] == "SK-08"


def test_mui_3_t08_a_blank_id_on_single_create_is_assigned(h: MastersHarness) -> None:
    first = h.post("SKILLS", {"payload": {"skill": "a"}}).json()
    second = h.post("SKILLS", {"payload": {"skill": "b"}}).json()
    assert (first["stable_id"], second["stable_id"]) == ("SK-01", "SK-02")
    assert (second["row_label"], second["payload"]["skill_id"]) == ("SK-02", "SK-02")
    helper = h.post("AGENTS", {"payload": {"agent_name": "h", "kind": "Assistant"}}).json()
    assert helper["stable_id"] == "AS-01"
    rule = h.post("RULES", {"payload": {"rule_type_description": "x"}}).json()
    assert rule["stable_id"] == "RULE-CUSTOM-001"


def test_mui_3_t09_filetype_is_frozen(h: MastersHarness) -> None:
    problem(bulk(h, "FILETYPE", [{"file_format": ".x"}]), 403, "FILETYPE_FROZEN")


def test_mui_3_t10_more_than_5000_rows_is_422(h: MastersHarness) -> None:
    problem(bulk(h, "SKILLS", [{"skill": "x"}] * 5001), 422, "VALIDATION_ERROR")
    assert stable_ids(h) == []


def test_mui_3_t11_fde_cannot_bulk(h: MastersHarness) -> None:
    res = bulk(h, "SKILLS", [SKILL], token=h.realm.token())
    assert res.status_code in (403, 404)
    assert stable_ids(h) == []


def test_mui_3_t12_admin_b_on_tenant_a_project_is_404(h: MastersHarness) -> None:
    project = h.create("A project")
    res = bulk(h, "SKILLS", [SKILL], project_id=project["id"], token=h.admin_b())
    problem(res, 404, "NOT_FOUND")
    out = report(bulk(h, "SKILLS", [SKILL], project_id=project["id"]))
    assert out["created"] == ["SK-QUOTE"]
    with h.sessions() as s:
        assert str(s.scalars(select(MasterEntry.project_id)).one()) == project["id"]


# --- retire ---------------------------------------------------------------------------------------


def test_mui_3_t13_retire_needs_if_match(h: MastersHarness) -> None:
    problem(retire(h, h.entry(), None), 428, "PRECONDITION_REQUIRED")


def test_mui_3_t14_retire_on_a_stale_etag_is_409(h: MastersHarness) -> None:
    entry = h.entry()
    assert h.version(entry, {**RULE_BOM, "owner_version": "v2"}, '"1"').status_code == 201
    problem(retire(h, entry, '"1"'), 409, "VERSION_CONFLICT")


def test_mui_3_t15_retired_is_hidden_unless_asked_and_keeps_its_versions(
    h: MastersHarness,
) -> None:
    entry = h.entry()
    res = retire(h, entry, '"1"')
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "RETIRED" and res.headers["etag"] == '"2"'
    assert h.call("GET", "/admin/masters/RULES", h.admin_a()).json()["items"] == []
    shown = h.call("GET", "/admin/masters/RULES?include_retired=true", h.admin_a()).json()
    assert [e["id"] for e in shown["items"]] == [entry["id"]]
    assert h.audit_actions(entry["id"]) == ["MASTER_ENTRY_CREATED", "MASTER_ENTRY_RETIRED"]
    with h.sessions() as s:
        assert len(s.scalars(select(MasterEntryVersion)).all()) == 1  # nothing deleted


def test_mui_3_t16_retire_is_idempotent(h: MastersHarness) -> None:
    entry = h.entry()
    assert retire(h, entry, '"1"').status_code == 200
    again = retire(h, entry, '"2"')
    assert again.status_code == 200 and again.headers["etag"] == '"2"'
    assert h.audit_actions(entry["id"]).count("MASTER_ENTRY_RETIRED") == 1


def test_mui_3_t17_admin_b_cannot_retire_tenant_a_entry(h: MastersHarness) -> None:
    entry = h.entry()
    problem(retire(h, entry, '"1"', token=h.admin_b()), 404, "NOT_FOUND")
    assert h.call("GET", f"/admin/masters/RULES/{entry['id']}", h.admin_a()).json()["status"] != (
        "RETIRED"
    )


def test_mui_3_t18_a_retired_id_is_never_assigned_again(h: MastersHarness) -> None:
    first = h.post("SKILLS", {"payload": {"skill": "a"}}).json()
    assert retire(h, first, '"1"').status_code == 200
    assert h.post("SKILLS", {"payload": {"skill": "b"}}).json()["stable_id"] == "SK-02"
