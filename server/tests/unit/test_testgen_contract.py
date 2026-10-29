"""E13.4 contract (TD §15.1): the shape of the test data, what the model may answer, the API bodies,
and the one canonical dataset hash. Pure models, no Docker."""

import copy
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.testgen import (
    GENERATED_KINDS,
    KIND_TERMINAL,
    MAX_ASSERTIONS,
    MAX_GENERATED_CASES,
    MAX_PASS_STEPS,
    MAX_STATEMENTS,
    CaseBody,
    CaseKind,
    CasePost,
    CasePut,
    ExpectedTerminal,
    TestDatasetOut,
    TestgenOutput,
    TestsResponse,
    dataset_sha256,
)


def case(kind: str, terminal: str, name: str | None = None, **over: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": name or f"{kind.lower()} case",
        "kind": kind,
        "input": {"customer_id": "C-1", "amount": 10},
        "expected_terminal": terminal,
    }
    return {**values, **over}


def default_cases() -> list[dict[str, Any]]:
    return [
        case("PASS_FIRST", "SUCCESS"),
        case("ESCALATION", "ESCALATED"),
        case("PASS_AFTER_RETRY", "SUCCESS", state_script=[["UPDATE t SET a = 1"]]),
    ]


def output(cases: list[dict[str, Any]] | None = None) -> TestgenOutput:
    return TestgenOutput.model_validate({"cases": default_cases() if cases is None else cases})


# --- what the model may answer --------------------------------------------------------------------


def test_e13_4_the_three_default_scenarios_validate() -> None:
    kinds = {c.kind for c in output().cases}
    assert kinds == {CaseKind.PASS_FIRST, CaseKind.ESCALATION, CaseKind.PASS_AFTER_RETRY}


@pytest.mark.parametrize("third", ["PASS_AFTER_RETRY", "NO_OP", "EDGE"])
def test_e13_4_any_of_the_three_optional_kinds_completes_the_set(third: str) -> None:
    terminal = {"PASS_AFTER_RETRY": "SUCCESS", "NO_OP": "NO_OP", "EDGE": "ESCALATED"}[third]
    cases = [case("PASS_FIRST", "SUCCESS"), case("ESCALATION", "ESCALATED"), case(third, terminal)]
    assert len(output(cases).cases) == 3


def test_e13_4_t03_fewer_than_three_cases_is_refused() -> None:
    with pytest.raises(ValidationError):
        output(default_cases()[:2])


@pytest.mark.parametrize("missing", ["PASS_FIRST", "ESCALATION"])
def test_e13_4_the_required_scenarios_cannot_be_left_out(missing: str) -> None:
    cases = [c for c in default_cases() if c["kind"] != missing]
    cases.append(case("EDGE", "SUCCESS", name="a boundary"))
    cases.append(case("NO_OP", "NO_OP", name="nothing to do"))
    with pytest.raises(ValidationError, match=missing):
        output(cases)


def test_e13_4_a_third_scenario_is_required() -> None:
    cases = [
        case("PASS_FIRST", "SUCCESS", name="first"),
        case("PASS_FIRST", "SUCCESS", name="again"),
        case("ESCALATION", "ESCALATED"),
    ]
    with pytest.raises(ValidationError, match="third scenario"):
        output(cases)


def test_e13_4_the_model_cannot_write_an_fde_case() -> None:
    cases = [*default_cases()[:2], case("FDE", "SUCCESS", name="mine")]
    with pytest.raises(ValidationError, match="not a kind the model may produce"):
        output(cases)


@pytest.mark.parametrize(
    ("kind", "wrong"),
    [("ESCALATION", "SUCCESS"), ("PASS_FIRST", "ESCALATED"), ("NO_OP", "SUCCESS")],
)
def test_e13_4_a_case_must_end_where_its_kind_ends(kind: str, wrong: str) -> None:
    odd = case(kind, wrong, name="odd one")
    with pytest.raises(ValidationError, match="ends in"):
        output([*default_cases(), odd])


def test_e13_4_an_edge_case_may_end_in_any_terminal_state() -> None:
    for terminal in ExpectedTerminal:
        edge = case("EDGE", terminal.value, name=f"edge {terminal.value}")
        assert output([*default_cases()[:2], edge]).cases[2].expected_terminal is terminal


def test_e13_4_every_generated_kind_has_a_terminal_rule() -> None:
    assert GENERATED_KINDS == set(CaseKind) - {CaseKind.FDE}
    assert set(KIND_TERMINAL) == set(CaseKind)


def test_e13_4_case_names_are_distinct() -> None:
    cases = default_cases()
    cases[2]["name"] = cases[0]["name"]
    with pytest.raises(ValidationError, match="distinct"):
        output(cases)


@pytest.mark.parametrize("field", ["synthetic", "status", "id", "dataset_id", "edited_by_fde"])
def test_e13_4_the_model_cannot_set_platform_fields(field: str) -> None:
    cases = default_cases()
    cases[0][field] = True
    with pytest.raises(ValidationError):
        output(cases)
    with pytest.raises(ValidationError):
        TestgenOutput.model_validate({"cases": default_cases(), field: True})


def with_each(field: str, value: Any) -> list[dict[str, Any]]:
    return [{**c, field: value} for c in default_cases()]


def test_e13_4_the_answer_is_bounded() -> None:
    edges = [case("EDGE", "SUCCESS", name=f"edge {i}") for i in range(MAX_GENERATED_CASES)]
    with pytest.raises(ValidationError):
        output([*default_cases()[:2], *edges])  # more than the answer may hold
    with pytest.raises(ValidationError):
        output(with_each("initial_state", ["INSERT INTO t VALUES (1)"] * (MAX_STATEMENTS + 1)))
    with pytest.raises(ValidationError):
        output(with_each("state_script", [[]] * (MAX_PASS_STEPS + 1)))
    with pytest.raises(ValidationError):
        output(with_each("assertions", [{"type": "x"}] * (MAX_ASSERTIONS + 1)))
    with pytest.raises(ValidationError):
        output(with_each("name", "n" * 121))


# --- the API bodies -------------------------------------------------------------------------------


def test_e13_4_a_put_changes_something_and_never_the_name_or_kind() -> None:
    assert CasePut.model_validate({"input": {"amount": 5}}).input == {"amount": 5}
    with pytest.raises(ValidationError, match="nothing to change"):
        CasePut.model_validate({})
    for field in ("name", "kind", "id", "synthetic"):
        with pytest.raises(ValidationError):
            CasePut.model_validate({"input": {}, field: "x"})


def test_e13_4_a_post_has_no_kind_the_server_sets_it() -> None:
    body = {"name": "my case", "input": {}, "expected_terminal": "SUCCESS"}
    assert CasePost.model_validate(body).name == "my case"
    with pytest.raises(ValidationError):
        CasePost.model_validate({**body, "kind": "PASS_FIRST"})
    with pytest.raises(ValidationError):
        CasePost.model_validate({**body, "synthetic": False})


def test_e13_4_a_dataset_is_always_synthetic() -> None:
    values: dict[str, Any] = {
        "version": 1,
        "sha256": "a" * 64,
        "edited_by_fde": False,
        "files_keys": [],
        "created_at": datetime.now(UTC),
        "cases": [],
    }
    assert TestDatasetOut.model_validate(values).synthetic is True
    with pytest.raises(ValidationError):
        TestDatasetOut.model_validate({**values, "synthetic": False})
    with pytest.raises(ValidationError):
        TestDatasetOut.model_validate({**values, "version": 0})
    with pytest.raises(ValidationError):
        TestDatasetOut.model_validate({**values, "sha256": "sha256:" + "a" * 64})
    assert TestsResponse().dataset is None  # before anything is generated


# --- the canonical hash ---------------------------------------------------------------------------

FILES = {"orders.csv": "1" * 64}
MOCKS = {"crm": "2" * 64}


def pairs() -> list[tuple[CaseKind, CaseBody]]:
    out = []
    for c in default_cases():
        body = {key: value for key, value in c.items() if key != "kind"}
        out.append((CaseKind(c["kind"]), CaseBody.model_validate(body)))
    return out


def test_e13_4_t09_the_hash_of_unchanged_data_is_the_same_every_time() -> None:
    first = dataset_sha256(pairs(), FILES, MOCKS)
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert dataset_sha256(pairs(), FILES, MOCKS) == first
    assert dataset_sha256(copy.deepcopy(pairs()), dict(FILES), dict(MOCKS)) == first


def test_e13_4_the_hash_does_not_depend_on_the_order_things_arrive_in() -> None:
    forward = dataset_sha256(pairs(), {"a": "1" * 64, "b": "2" * 64}, {})
    backward = dataset_sha256(list(reversed(pairs())), {"b": "2" * 64, "a": "1" * 64}, {})
    assert forward == backward


def test_e13_4_any_edit_gives_another_hash() -> None:
    base = dataset_sha256(pairs(), FILES, MOCKS)
    edited = pairs()
    edited[0][1].input["amount"] = 11
    assert dataset_sha256(edited, FILES, MOCKS) != base
    renamed = pairs()
    renamed[1] = (renamed[1][0], renamed[1][1].model_copy(update={"name": "renamed"}))
    assert dataset_sha256(renamed, FILES, MOCKS) != base
    reclassed = pairs()
    reclassed[0] = (CaseKind.FDE, reclassed[0][1])
    assert dataset_sha256(reclassed, FILES, MOCKS) != base
    assert dataset_sha256(pairs(), {"orders.csv": "3" * 64}, MOCKS) != base  # a file changed
    assert dataset_sha256(pairs(), FILES, {"crm": "4" * 64}) != base  # a mock changed
    assert dataset_sha256(pairs()[:2], FILES, MOCKS) != base  # a case was removed


def test_e13_4_files_and_mocks_are_optional() -> None:
    assert dataset_sha256(pairs()) == dataset_sha256(pairs(), {}, {})
