"""MUI-3 (#782): the template lists, `GET/PUT /admin/master-lists/{key}`, through the real app."""

import csv
import io
from pathlib import Path
from typing import Any

import pytest
import test_projects
from sbam_api.audit.models import AuditLog
from sbam_api.masters.models import MasterListRow
from sbam_contracts.masters_v03 import LIST_DEFAULT_HEADERS, MasterListKey
from sqlalchemy import select
from test_auth import problem
from test_masters_api import MastersHarness, engine_with_masters

EMPLOYEES = {"headers": ["Employee", "Job title"], "rows": [["Asha", "Buyer"], ["Ravi", "QA"]]}


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MastersHarness:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_masters)
    return MastersHarness(tmp_path)


def put(h: MastersHarness, body: dict[str, Any], etag: str | None, token: str = "") -> Any:
    headers = {"if-match": etag} if etag is not None else {}
    return h.call(
        "PUT", "/admin/master-lists/employees", token or h.admin_a(), json=body, headers=headers
    )


def test_mui_3_t20_an_empty_list_answers_its_default_headers(h: MastersHarness) -> None:
    res = h.call("GET", "/admin/master-lists/customers", h.admin_a())
    assert res.status_code == 200 and res.headers["etag"] == '"0"'
    assert res.json() == {
        "headers": list(LIST_DEFAULT_HEADERS[MasterListKey.CUSTOMERS]),
        "rows": [],
    }


def test_mui_3_t21_first_put_with_if_match_0_then_replace(h: MastersHarness) -> None:
    res = put(h, EMPLOYEES, '"0"')
    assert res.status_code == 200, res.text
    assert res.json() == EMPLOYEES and res.headers["etag"] == '"1"'
    again = put(h, {"headers": ["Name"], "rows": []}, '"1"')
    assert again.status_code == 200 and again.headers["etag"] == '"2"'
    got = h.call("GET", "/admin/master-lists/employees", h.admin_a())
    assert got.json() == {"headers": ["Name"], "rows": []} and got.headers["etag"] == '"2"'
    with h.sessions() as s:
        assert len(s.scalars(select(MasterListRow)).all()) == 1


def test_mui_3_t22_a_stale_if_match_is_409(h: MastersHarness) -> None:
    put(h, EMPLOYEES, '"0"')
    problem(put(h, EMPLOYEES, '"0"'), 409, "VERSION_CONFLICT")  # a second "first" save


def test_mui_3_t23_a_missing_if_match_is_428(h: MastersHarness) -> None:
    problem(put(h, EMPLOYEES, None), 428, "PRECONDITION_REQUIRED")


def test_mui_3_t24_csv_download(h: MastersHarness) -> None:
    put(h, {"headers": ["Employee", "Note"], "rows": [["Asha", "=HYPERLINK(1)"]]}, '"0"')
    res = h.call("GET", "/admin/master-lists/employees?format=csv", h.admin_a())
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert "employees.csv" in res.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(res.text)))
    assert rows == [["Employee", "Note"], ["Asha", "'=HYPERLINK(1)"]]  # no formula runs


def test_mui_3_t25_an_unknown_key_is_404(h: MastersHarness) -> None:
    problem(h.call("GET", "/admin/master-lists/vendors", h.admin_a()), 404, "NOT_FOUND")
    problem(
        h.call("PUT", "/admin/master-lists/vendors", h.admin_a(), json=EMPLOYEES), 404, "NOT_FOUND"
    )


def test_mui_3_t26_tenant_b_never_sees_tenant_a_list(h: MastersHarness) -> None:
    put(h, EMPLOYEES, '"0"')
    res = h.call("GET", "/admin/master-lists/employees", h.admin_b())
    assert res.json()["rows"] == [] and res.headers["etag"] == '"0"'
    assert put(h, {"headers": ["B"], "rows": [["b"]]}, '"0"', token=h.admin_b()).status_code == 200
    a = h.call("GET", "/admin/master-lists/employees", h.admin_a()).json()
    assert a == EMPLOYEES


def test_mui_3_t27_replacing_a_list_is_audited(h: MastersHarness) -> None:
    put(h, EMPLOYEES, '"0"')
    with h.sessions() as s:
        row = s.scalars(select(AuditLog).where(AuditLog.action == "MASTER_LIST_REPLACED")).one()
    assert row.after == {"key": "employees", "columns": 2, "rows": 2}


def test_mui_3_t28_a_ragged_list_is_422_and_fde_is_refused(h: MastersHarness) -> None:
    problem(put(h, {"headers": ["A", "B"], "rows": [["only one"]]}, '"0"'), 422, "VALIDATION_ERROR")
    res = h.call("GET", "/admin/master-lists/employees", h.realm.token())
    assert res.status_code in (403, 404)
