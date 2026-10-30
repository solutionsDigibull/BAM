"""E9.1b: the Admin's per-tenant SRIM runtime defaults (`/admin/srim-defaults`, O-19)."""

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.srim_defaults.models import TenantSrimDefaults
from sbam_api.srim_defaults.repository import SrimDefaultsRepository
from sbam_contracts.manifest import ISA14_SRIM_DEFAULTS
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_auth import FDE_A1, TENANT_A, problem
from test_projects import Harness as Base

URL = "/admin/srim-defaults"
ISA14 = ISA14_SRIM_DEFAULTS.model_dump(mode="json")
CUSTOM: dict[str, Any] = {
    "orchestration": {"router": "Acme_Router", "trigger": "Acme_Trigger"},
    "execution": {"engine": "Acme_Engine"},
    "gateway": {"route_via": "Acme_Gateway"},
    "persistence": {"database": "Acme_DB", "action": "Acme_SQL"},
    "audit": {"telemetry": "Acme_Audit"},
    "ui": {"target": "Acme_Canvas", "method": "Acme_Widgets"},
}


class Harness(Base):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        TenantSrimDefaults.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]

    def put(self, body: Any, version: str | None = '"0"', token: str | None = None) -> Any:
        headers = {} if version is None else {"if-match": version}
        return self.call("PUT", URL, token or self.admin_a(), headers=headers, json=body)

    def srim_audit(self) -> list[AuditLog]:
        with self.sessions() as s:
            rows = list(
                s.scalars(select(AuditLog).where(AuditLog.entity_type == "tenant_srim_defaults"))
            )
            s.expunge_all()
            return rows


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e9_1b_t01_a_tenant_that_set_nothing_reads_the_isa14_defaults(h: Harness) -> None:
    res = h.call("GET", URL, h.admin_a())
    assert res.status_code == 200, res.text
    assert res.json() == {"defaults": ISA14, "is_default": True}
    assert res.headers["etag"] == '"0"'


def test_e9_1b_t02_a_write_needs_if_match_and_a_stale_one_is_412(h: Harness) -> None:
    problem(h.put(CUSTOM, version=None), 428, "PRECONDITION_REQUIRED")
    problem(h.put(CUSTOM, version='"7"'), 412, "PRECONDITION_FAILED")
    assert h.srim_audit() == []
    assert h.call("GET", URL, h.admin_a()).json()["is_default"] is True


def test_e9_1b_t03_the_first_write_creates_the_row_and_is_audited(h: Harness) -> None:
    res = h.put(CUSTOM)
    assert res.status_code == 200, res.text
    assert res.json() == {"defaults": CUSTOM, "is_default": False}
    assert res.headers["etag"] == '"1"'
    got = h.call("GET", URL, h.admin_a())
    assert got.json() == {"defaults": CUSTOM, "is_default": False} and got.headers["etag"] == '"1"'
    [row] = h.srim_audit()
    assert row.action == "SRIM_DEFAULTS_UPDATED" and row.tenant_id == TENANT_A
    assert row.before is None and row.after == CUSTOM


def test_e9_1b_t04_an_update_needs_the_current_etag_and_records_before_and_after(
    h: Harness,
) -> None:
    h.put(CUSTOM)
    newer = copy.deepcopy(CUSTOM)
    newer["gateway"]["route_via"] = "Acme_Gateway_v2"
    problem(h.put(newer, version='"0"'), 412, "PRECONDITION_FAILED")  # the first writer's view
    res = h.put(newer, version='"1"')
    assert res.status_code == 200 and res.headers["etag"] == '"2"'
    before, after = h.srim_audit()[-1].before, h.srim_audit()[-1].after
    assert before == CUSTOM and after == newer


def test_e9_1b_t05_saving_the_same_values_again_changes_nothing(h: Harness) -> None:
    h.put(CUSTOM)
    res = h.put(CUSTOM, version='"1"')
    assert res.status_code == 200 and res.headers["etag"] == '"1"'
    assert len(h.srim_audit()) == 1


@pytest.mark.parametrize(
    "block", ["orchestration", "execution", "gateway", "persistence", "audit", "ui"]
)
def test_e9_1b_t06_a_missing_block_is_422_and_nothing_is_written(h: Harness, block: str) -> None:
    body = copy.deepcopy(CUSTOM)
    del body[block]
    problem(h.put(body), 422, "VALIDATION_ERROR")
    assert h.call("GET", URL, h.admin_a()).json()["is_default"] is True and h.srim_audit() == []


@pytest.mark.parametrize("value", ["", "   ", None, 3])
def test_e9_1b_t07_an_empty_or_non_string_value_is_422(h: Harness, value: Any) -> None:
    body = copy.deepcopy(CUSTOM)
    body["ui"]["method"] = value
    problem(h.put(body), 422, "VALIDATION_ERROR")


def test_e9_1b_t08_the_derived_table_and_unknown_keys_cannot_be_set(h: Harness) -> None:
    body = copy.deepcopy(CUSTOM)
    body["persistence"]["table"] = "my_table"
    problem(h.put(body), 422, "VALIDATION_ERROR")
    body = copy.deepcopy(CUSTOM)
    body["tenant_id"] = "someone-else"
    problem(h.put(body), 422, "VALIDATION_ERROR")


def test_e9_1b_t09_each_tenant_has_its_own_values(h: Harness) -> None:
    h.put(CUSTOM)
    other = h.call("GET", URL, h.admin_b())
    assert other.json() == {"defaults": ISA14, "is_default": True}
    assert h.put(copy.deepcopy(CUSTOM), token=h.admin_b()).status_code == 200  # "0" is B's too
    assert h.call("GET", URL, h.admin_a()).headers["etag"] == '"1"'


def test_e9_1b_t10_only_an_admin_may_read_or_write(h: Harness) -> None:
    fde = h.realm.token(FDE_A1, realm_access={"roles": ["sbam-fde"]})
    for method, kwargs in (("GET", {}), ("PUT", {"json": CUSTOM, "headers": {"if-match": '"0"'}})):
        problem(h.call(method, URL, fde, **kwargs), 403, "WRONG_PORTAL")
    assert h.client.get(f"/api/v1{URL}").status_code == 401


def test_e9_1b_t11_two_first_writers_one_wins_the_other_is_412(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h.put(CUSTOM)  # the other admin's insert is already committed...
    real = SrimDefaultsRepository.row
    monkeypatch.setattr(  # ...while this request still saw "no row" when it looked
        SrimDefaultsRepository, "row", lambda self, for_update=False: None
    )
    other = copy.deepcopy(CUSTOM)
    other["audit"]["telemetry"] = "Other"
    problem(h.put(other), 412, "PRECONDITION_FAILED")
    monkeypatch.setattr(SrimDefaultsRepository, "row", real)
    assert h.call("GET", URL, h.admin_a()).json()["defaults"] == CUSTOM


def test_e9_1b_t12_a_database_unique_violation_is_the_only_way_to_two_rows(h: Harness) -> None:
    h.put(CUSTOM)
    with h.sessions() as s, pytest.raises(IntegrityError):
        s.add(TenantSrimDefaults(tenant_id=TENANT_A, defaults=CUSTOM))
        s.commit()


def test_e9_1b_t13_reset_returns_the_tenant_to_the_isa14_values_and_is_audited(h: Harness) -> None:
    h.put(CUSTOM)
    problem(h.call("DELETE", URL, h.admin_a()), 428, "PRECONDITION_REQUIRED")
    problem(
        h.call("DELETE", URL, h.admin_a(), headers={"if-match": '"0"'}), 412, "PRECONDITION_FAILED"
    )
    res = h.call("DELETE", URL, h.admin_a(), headers={"if-match": '"1"'})
    assert res.status_code == 200 and res.json() == {"defaults": ISA14, "is_default": True}
    assert res.headers["etag"] == '"0"'
    assert h.call("GET", URL, h.admin_a()).json()["is_default"] is True
    assert [r.action for r in h.srim_audit()] == ["SRIM_DEFAULTS_UPDATED", "SRIM_DEFAULTS_RESET"]
    assert h.srim_audit()[-1].before == CUSTOM and h.srim_audit()[-1].after is None
    assert h.put(CUSTOM).status_code == 200  # the tenant can set values again from "0"


def test_e9_1b_t14_resetting_a_tenant_that_never_set_values_is_a_quiet_no_op(h: Harness) -> None:
    res = h.call("DELETE", URL, h.admin_a(), headers={"if-match": '"0"'})
    assert res.status_code == 200 and res.json()["is_default"] is True
    assert h.srim_audit() == []


def test_e9_1b_t15_one_tenants_reset_leaves_the_other_tenants_values(h: Harness) -> None:
    h.put(CUSTOM)
    h.put(copy.deepcopy(CUSTOM), token=h.admin_b())
    h.call("DELETE", URL, h.admin_a(), headers={"if-match": '"1"'})
    assert h.call("GET", URL, h.admin_b()).json() == {"defaults": CUSTOM, "is_default": False}
