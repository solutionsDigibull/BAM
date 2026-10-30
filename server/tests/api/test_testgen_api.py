"""E13.4 test data endpoints through the real app and auth chain: read a Sub-Skill's dataset, and
add, change or delete a case. Every write is a new dataset version, validated, ETagged and audited.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.isatvon.models import SubSkill
from sbam_api.testgen.models import CaseRow, DatasetRow
from sbam_api.testgen.service import mocks_hashes
from sbam_contracts.testgen import CaseBody, CaseKind, ExpectedTerminal, dataset_sha256
from sqlalchemy import select
from test_auth import TENANT_A, TENANT_B, problem
from test_isatvon_validate_api import Harness as ValidateHarness
from test_isatvon_validate_api import sample

INPUT = {"mobile_number": "9999900000", "otp_payload": "123456", "device_fingerprint": "fp-1"}
KINDS = (
    (CaseKind.PASS_FIRST, ExpectedTerminal.SUCCESS),
    (CaseKind.ESCALATION, ExpectedTerminal.ESCALATED),
    (CaseKind.NO_OP, ExpectedTerminal.NO_OP),
)


def body(name: str, terminal: ExpectedTerminal) -> CaseBody:
    return CaseBody.model_validate({"name": name, "input": INPUT, "expected_terminal": terminal})


class Harness(ValidateHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        bind = self.sessions.kw["bind"]
        for table in (DatasetRow, CaseRow, AuditLog):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.sub = self.add_sub_skill(sample(), state="VALID")

    def seed(self, sub: uuid.UUID | None = None, mocks: list[str] | None = None) -> str:
        cases = [(kind, body(f"case-{kind.value}", terminal)) for kind, terminal in KINDS]
        sha = dataset_sha256(cases, {}, mocks_hashes(mocks or []))
        with self.sessions() as s:
            dataset = DatasetRow(
                tenant_id=TENANT_A,
                project_id=uuid.UUID(self.project),
                sub_skill_id=sub or self.sub,
                version=1,
                files_keys=[],
                mocks_keys=mocks or [],
                sha256=sha,
                synthetic=True,
                edited_by_fde=False,
                generated_for="f" * 64,
            )
            s.add(dataset)
            s.flush()
            for kind, case in cases:
                s.add(
                    CaseRow(
                        tenant_id=TENANT_A,
                        project_id=uuid.UUID(self.project),
                        dataset_id=dataset.id,
                        name=case.name,
                        kind=kind.value,
                        input=case.input,
                        initial_state=[],
                        state_script=[],
                        expected_terminal=case.expected_terminal.value,
                        expected_output=None,
                        assertions=[],
                    )
                )
            s.commit()
        return sha

    def url(self, suffix: str = "", sub: uuid.UUID | None = None) -> str:
        base = f"/projects/{self.project}/workflows/{self.wf}/sub-skills/{sub or self.sub}"
        return f"{base}/tests{suffix}"

    def tests(self, token: str | None = None, sub: uuid.UUID | None = None) -> Any:
        return self.call("GET", self.url(sub=sub), token or self.fde())

    def write(
        self,
        method: str,
        suffix: str,
        sha: str | None,
        payload: Any = None,
        token: str | None = None,
        sub: uuid.UUID | None = None,
    ) -> Any:
        headers = {} if sha is None else {"If-Match": f'"{sha}"'}
        return self.call(
            method, self.url(suffix, sub), token or self.fde(), headers=headers, json=payload
        )

    def case_id(self, name: str) -> str:
        cases = self.tests().json()["dataset"]["cases"]
        return str(next(c["id"] for c in cases if c["name"] == name))

    def dataset_rows(self) -> list[DatasetRow]:
        with self.sessions() as s:
            return list(s.scalars(select(DatasetRow).order_by(DatasetRow.version)))

    def set_sub_state(self, state: str) -> None:
        with self.sessions() as s:
            row = s.get(SubSkill, self.sub)
            assert row is not None
            row.state = state
            s.commit()


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e13_4_t01_no_dataset_yet_reads_as_none(h: Harness) -> None:
    assert h.tests().json() == {"dataset": None, "input_schema": None}


def test_e13_4_t02_get_returns_the_cases_and_the_hash_as_etag(h: Harness) -> None:
    sha = h.seed()
    res = h.tests()
    assert res.status_code == 200, res.text
    data = res.json()["dataset"]
    assert data["sha256"] == sha and data["synthetic"] is True and data["version"] == 1
    assert {c["kind"] for c in data["cases"]} == {k.value for k, _ in KINDS}
    assert res.headers["ETag"].strip('"') == sha


def test_e13_4_t03_edit_writes_a_new_version_and_keeps_the_old(h: Harness) -> None:
    sha = h.seed()
    cid = h.case_id("case-PASS_FIRST")
    changed = {**INPUT, "otp_payload": "654321"}
    res = h.write("PUT", f"/cases/{cid}", sha, {"input": changed})
    assert res.status_code == 200, res.text
    assert res.json()["input"] == changed
    v1, v2 = h.dataset_rows()
    assert (v1.version, v2.version, v2.edited_by_fde) == (1, 2, True)
    assert v2.generated_for == v1.generated_for == "f" * 64  # an edit is not a new ISATVON
    assert v2.sha256 != v1.sha256 and res.headers["ETag"].strip('"') == v2.sha256
    with h.sessions() as s:
        assert "TEST_CASE_EDIT" in [a.action for a in s.scalars(select(AuditLog))]
    assert h.tests().json()["dataset"]["version"] == 2


def test_e13_4_t04_an_edit_that_changes_nothing_writes_nothing(h: Harness) -> None:
    sha = h.seed()
    res = h.write("PUT", f"/cases/{h.case_id('case-PASS_FIRST')}", sha, {"input": INPUT})
    assert res.status_code == 200 and len(h.dataset_rows()) == 1


def test_e13_4_t05_if_match_is_required_and_must_match(h: Harness) -> None:
    sha = h.seed()
    cid = h.case_id("case-PASS_FIRST")
    payload = {"input": {**INPUT, "otp_payload": "1"}}
    problem(h.write("PUT", f"/cases/{cid}", None, payload), 428, "PRECONDITION_REQUIRED")
    problem(h.write("PUT", f"/cases/{cid}", "0" * 64, payload), 412, "PRECONDITION_FAILED")
    assert h.write("PUT", f"/cases/{cid}", sha, payload).status_code == 200
    problem(h.write("PUT", f"/cases/{cid}", sha, payload), 412, "PRECONDITION_FAILED")  # stale


def test_e13_4_t06_input_the_sub_skill_does_not_declare_is_422_with_the_path(h: Harness) -> None:
    sha = h.seed()
    cid = h.case_id("case-PASS_FIRST")
    res = h.write("PUT", f"/cases/{cid}", sha, {"input": {**INPUT, "typo": 1}})
    problem(res, 422, "VALIDATION_ERROR")
    assert "/input/typo" in [e["path"] for e in res.json()["errors"]]
    res = h.write("PUT", f"/cases/{cid}", sha, {"input": {"mobile_number": 5}})
    paths = [e["path"] for e in res.json()["errors"]]
    assert "/input/mobile_number" in paths and "/input/otp_payload" in paths
    assert len(h.dataset_rows()) == 1


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE investor_profiles",
        "DELETE FROM investor_profiles",
        "INSERT INTO other_table (a) VALUES (1)",
        "INSERT INTO investor_profiles (a) VALUES (1); DROP TABLE x",
        "INSERT INTO investor_profiles (a) SELECT 1",
        "INSERT INTO investor_profiles (a) VALUES (pg_sleep(10))",
        "INSERT INTO sbam.investor_profiles (a) VALUES (1)",
    ],
)
def test_e13_4_t07_seed_sql_outside_the_allowlist_is_422(h: Harness, sql: str) -> None:
    sha = h.seed()
    cid = h.case_id("case-PASS_FIRST")
    res = h.write("PUT", f"/cases/{cid}", sha, {"initial_state": [sql]})
    problem(res, 422, "VALIDATION_ERROR")
    assert res.json()["errors"][0]["path"] == "/initial_state/0"
    assert len(h.dataset_rows()) == 1


def test_e13_4_t07_allowed_seed_sql_is_stored_and_the_row_budget_is_enforced(h: Harness) -> None:
    sha = h.seed()
    cid = h.case_id("case-PASS_FIRST")
    ok = "INSERT INTO investor_profiles (id) VALUES (1), (2)"
    res = h.write("PUT", f"/cases/{cid}", sha, {"initial_state": [ok]})
    assert res.status_code == 200, res.text
    cid = res.json()["id"]  # a new version has new case ids
    rows = ", ".join(f"({i})" for i in range(501))
    many = f"INSERT INTO investor_profiles (id) VALUES {rows}"  # noqa: S608
    res = h.write("PUT", f"/cases/{cid}", res.headers["ETag"].strip('"'), {"initial_state": [many]})
    problem(res, 422, "VALIDATION_ERROR")


def test_e13_4_t07_a_terminal_the_kind_cannot_reach_is_422(h: Harness) -> None:
    sha = h.seed()
    cid = h.case_id("case-ESCALATION")
    res = h.write("PUT", f"/cases/{cid}", sha, {"expected_terminal": "SUCCESS"})
    problem(res, 422, "VALIDATION_ERROR")
    assert res.json()["errors"][0]["path"] == "/expected_terminal"


def test_e13_4_t08_add_and_delete_a_case(h: Harness) -> None:
    sha = h.seed()
    new = {"name": "mine", "input": INPUT, "expected_terminal": "SUCCESS"}
    res = h.write("POST", "/cases", sha, new)
    assert res.status_code == 201, res.text
    assert res.json()["kind"] == "FDE"
    sha2 = res.headers["ETag"].strip('"')
    problem(h.write("POST", "/cases", sha2, new), 422, "VALIDATION_ERROR")  # name taken
    res = h.write("DELETE", f"/cases/{res.json()['id']}", sha2)
    assert res.status_code == 204
    assert len(h.tests().json()["dataset"]["cases"]) == 3


def test_e13_4_t08_the_last_three_cases_cannot_be_deleted(h: Harness) -> None:
    sha = h.seed()
    res = h.write("DELETE", f"/cases/{h.case_id('case-NO_OP')}", sha)
    problem(res, 409, "MIN_TEST_CASES")
    assert len(h.dataset_rows()) == 1


def test_e13_4_no_dataset_yet_is_409_on_a_write(h: Harness) -> None:
    res = h.write(
        "POST", "/cases", "0" * 64, {"name": "x", "input": INPUT, "expected_terminal": "SUCCESS"}
    )
    problem(res, 409, "CONFLICT")


@pytest.mark.parametrize("state", ["CERTIFIED", "FROZEN", "SUPERSEDED"])
def test_e13_4_a_locked_sub_skill_is_423(h: Harness, state: str) -> None:
    sha = h.seed()
    h.set_sub_state(state)
    res = h.write("DELETE", f"/cases/{h.case_id('case-NO_OP')}", sha)
    problem(res, 423, "RESOURCE_LOCKED")
    assert len(h.dataset_rows()) == 1


def test_e13_4_another_tenant_or_workflow_or_unknown_sub_skill_is_404(h: Harness) -> None:
    sha = h.seed()
    cid = h.case_id("case-PASS_FIRST")
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    problem(h.tests(other), 404, "NOT_FOUND")
    problem(h.write("PUT", f"/cases/{cid}", sha, {"input": INPUT}, token=other), 404, "NOT_FOUND")
    problem(h.tests(sub=uuid.uuid4()), 404, "NOT_FOUND")
    other_wf = h.workflow(2, sow_version=h.generated(h.structured))
    wrong = f"/projects/{h.project}/workflows/{other_wf}/sub-skills/{h.sub}/tests"
    problem(h.call("GET", wrong, h.fde()), 404, "NOT_FOUND")
    problem(h.write("PUT", f"/cases/{uuid.uuid4()}", sha, {"input": INPUT}), 404, "NOT_FOUND")
    assert len(h.dataset_rows()) == 1


MOCK_KEYS = [f"t/p/w/tests/{'a' * 64}", f"t/p/w/tests/{'b' * 64}"]


def test_e13_5_the_dataset_read_lists_its_mock_definitions(h: Harness) -> None:
    h.seed(mocks=MOCK_KEYS)
    assert h.tests().json()["dataset"]["mocks_keys"] == MOCK_KEYS


def test_e13_5_an_fde_edit_keeps_the_mocks_and_the_new_hash_covers_them(h: Harness) -> None:
    sha = h.seed(mocks=MOCK_KEYS)
    cid = h.case_id("case-PASS_FIRST")
    res = h.write("PUT", f"/cases/{cid}", sha, {"input": {**INPUT, "otp_payload": "654321"}})
    assert res.status_code == 200, res.text
    v1, v2 = h.dataset_rows()
    assert v2.mocks_keys == MOCK_KEYS and v1.mocks_keys == MOCK_KEYS  # the edit leaves them alone
    cases = [(k, body(f"case-{k.value}", t)) for k, t in KINDS]
    cases[0] = (
        cases[0][0],
        body("case-PASS_FIRST", ExpectedTerminal.SUCCESS).model_copy(
            update={"input": {**INPUT, "otp_payload": "654321"}}
        ),
    )
    assert v2.sha256 == dataset_sha256(cases, {}, mocks_hashes(MOCK_KEYS))
    assert v2.sha256 != v1.sha256


def test_e13_5_the_mock_hash_ignores_which_folder_a_definition_is_stored_under() -> None:
    elsewhere = [f"other-tenant/other-project/other-wf/tests/{'a' * 64}", f"x/tests/{'b' * 64}"]
    assert (
        mocks_hashes(MOCK_KEYS)
        == mocks_hashes(elsewhere)
        == {"a" * 64: "a" * 64, "b" * 64: "b" * 64}
    )


def test_e13_6_an_assertion_type_the_suite_does_not_know_is_422_with_its_path(h: Harness) -> None:
    sha = h.seed()
    cid = h.case_id("case-PASS_FIRST")
    res = h.write("PUT", f"/cases/{cid}", sha, {"assertions": [{"type": "looks_fine"}]})
    problem(res, 422, "VALIDATION_ERROR")
    assert res.json()["errors"][0]["path"] == "/assertions/0/type"
    ok = h.write("PUT", f"/cases/{cid}", sha, {"assertions": [{"type": "iterations_within_bound"}]})
    assert ok.status_code == 200, ok.text


def test_e13_7_t03_the_server_keeps_three_cases_even_when_the_ui_is_bypassed(h: Harness) -> None:
    sha = h.seed()
    for name in ("case-PASS_FIRST", "case-ESCALATION", "case-NO_OP"):
        problem(h.write("DELETE", f"/cases/{h.case_id(name)}", sha), 409, "MIN_TEST_CASES")
    assert len(h.tests().json()["dataset"]["cases"]) == 3
    assert len(h.dataset_rows()) == 1


def test_e13_7_t08_a_direct_put_on_a_frozen_sub_skill_is_423(h: Harness) -> None:
    sha = h.seed()
    h.set_sub_state("FROZEN")
    res = h.write("PUT", f"/cases/{h.case_id('case-NO_OP')}", sha, {"input": INPUT})
    problem(res, 423, "RESOURCE_LOCKED")
    assert len(h.dataset_rows()) == 1


def test_e13_7_the_tests_answer_carries_the_closed_input_schema(h: Harness) -> None:
    h.seed()
    schema = h.tests().json()["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(INPUT) <= set(schema["properties"])
