"""E13.6 contract: the suite, its canonical hash, a stored run and the grid badge."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.build_dashboard import BuildRow
from sbam_contracts.promptfoo import (
    DETERMINISTIC,
    AssertionType,
    LoopTestsBadge,
    Suite,
    TestRunOut,
    TestRunsResponse,
    TestRunState,
    badge_of,
    suite_sha256,
)


def case(
    name: str, kind: str = "PASS_FIRST", terminal: str = "SUCCESS", **over: Any
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "input": {"amount": 1},
        "expected_terminal": terminal,
        "assertions": [{"type": "terminal_state"}, {"type": "output_schema"}],
        **over,
    }


def suite_data(**over: Any) -> dict[str, Any]:
    return {
        "bounds": {"max_iterations": 3, "budget_ms": 1000, "handoff_fields": ["iteration_pass"]},
        "output_schema": {"type": "object", "required": ["state_handoff_id"]},
        "cases": [
            case("a"),
            case("b", "ESCALATION", "ESCALATED"),
            case("c", "NO_OP", "NO_OP"),
        ],
        **over,
    }


def run_data(**over: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "sub_skill_id": uuid.uuid4(),
        "code_attempt_id": uuid.uuid4(),
        "sandbox_run_id": uuid.uuid4(),
        "suite_sha256": "a" * 64,
        "state": "PASS",
        "total": 3,
        "passed": 3,
        "failed": 0,
        "results": [],
        "created_at": datetime(2026, 10, 29, tzinfo=UTC),
        **over,
    }


def test_e13_6_t09_the_same_suite_gives_the_same_hash_in_any_case_order() -> None:
    first = Suite.model_validate(suite_data())
    second = Suite.model_validate(suite_data(cases=list(reversed(suite_data()["cases"]))))
    assert suite_sha256(first) == suite_sha256(second)
    assert len(suite_sha256(first)) == 64


def test_e13_6_any_change_to_the_suite_changes_the_hash() -> None:
    base = suite_sha256(Suite.model_validate(suite_data()))
    changed = suite_data()
    changed["bounds"]["max_iterations"] = 4
    assert suite_sha256(Suite.model_validate(changed)) != base
    cases = suite_data()["cases"]
    cases[0]["input"] = {"amount": 2}
    assert suite_sha256(Suite.model_validate(suite_data(cases=cases))) != base


def test_e13_6_a_suite_needs_three_distinct_cases() -> None:
    with pytest.raises(ValidationError):
        Suite.model_validate(suite_data(cases=suite_data()["cases"][:2]))
    with pytest.raises(ValidationError):
        Suite.model_validate(suite_data(cases=[case("a"), case("a"), case("a")]))


def test_e13_6_t08_a_rubric_never_comes_first_or_before_a_deterministic_assertion() -> None:
    rubric = {"type": "llm_rubric", "value": "polite"}
    ok = case("r", assertions=[{"type": "output_schema"}, rubric])
    assert Suite.model_validate(suite_data(cases=[ok, case("b"), case("c")]))
    first = case("r", assertions=[rubric, {"type": "output_schema"}])
    with pytest.raises(ValidationError):
        Suite.model_validate(suite_data(cases=[first, case("b"), case("c")]))
    only = case("r", assertions=[rubric])
    with pytest.raises(ValidationError):
        Suite.model_validate(suite_data(cases=[only, case("b"), case("c")]))


def test_e13_6_the_deterministic_set_is_everything_but_the_rubric() -> None:
    assert DETERMINISTIC == set(AssertionType) - {AssertionType.LLM_RUBRIC}


def test_e13_6_a_suite_refuses_unknown_fields_and_types() -> None:
    with pytest.raises(ValidationError):
        Suite.model_validate({**suite_data(), "threshold": 0.9})
    bad = case("x", assertions=[{"type": "anything_goes"}])
    with pytest.raises(ValidationError):
        Suite.model_validate(suite_data(cases=[bad, case("b"), case("c")]))


def test_e13_6_t02_a_green_run_is_a_pass_badge_three_of_three() -> None:
    run = TestRunOut.model_validate(run_data())
    assert badge_of(run) == LoopTestsBadge(state=TestRunState.PASS, passed=3, total=3)


def test_e13_6_totals_must_add_up_and_cover_three_cases() -> None:
    with pytest.raises(ValidationError):
        TestRunOut.model_validate(run_data(passed=2, failed=0))
    with pytest.raises(ValidationError):
        TestRunOut.model_validate(run_data(total=2, passed=2))


def test_e13_6_there_is_no_percentage_threshold_a_failed_case_is_not_a_pass() -> None:
    with pytest.raises(ValidationError):
        TestRunOut.model_validate(run_data(state="PASS", passed=2, failed=1))
    assert TestRunOut.model_validate(run_data(state="FAIL", passed=2, failed=1))


def test_e13_6_a_non_certifying_run_is_never_a_pass() -> None:
    with pytest.raises(ValidationError):
        TestRunOut.model_validate(run_data(non_certifying=True))
    run = TestRunOut.model_validate(run_data(state="NON_CERTIFYING", non_certifying=True))
    assert badge_of(run).state is TestRunState.NON_CERTIFYING


def test_e13_6_the_grid_row_carries_an_optional_badge() -> None:
    row: dict[str, Any] = {
        "sub_skill_id": uuid.uuid4(),
        "skill_id": "MS01.1_X",
        "skill_name": "X",
        "state": "BUILDING",
        "stage": "ast_gate",
    }
    assert BuildRow.model_validate(row).loop_tests is None
    with_badge = {**row, "loop_tests": {"state": "FAIL", "passed": 1, "total": 3}}
    assert BuildRow.model_validate(with_badge).loop_tests == LoopTestsBadge(
        state=TestRunState.FAIL, passed=1, total=3
    )
    with pytest.raises(ValidationError):
        LoopTestsBadge(state=TestRunState.PASS, passed=4, total=3)


def test_e13_6_a_sub_skill_with_no_runs_has_an_empty_list() -> None:
    assert TestRunsResponse().runs == []
    assert TestRunsResponse(runs=[TestRunOut.model_validate(run_data())]).runs[0].total == 3
