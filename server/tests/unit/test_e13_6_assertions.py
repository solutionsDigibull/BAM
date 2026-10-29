"""E13.6: the deterministic assertions, on hand-built harness evidence (no sandbox, no model)."""

from typing import Any

import pytest
from sbam_contracts.promptfoo import AssertionType, SuiteAssertion, SuiteBounds, SuiteCase
from sbam_contracts.testgen import CaseKind
from sbam_workers.test.assertions import (
    Context,
    Evidence,
    case_passed,
    judge_case,
    run_assertion,
)
from sbam_workers.test.harness import HarnessTrace

SCHEMA = {
    "type": "object",
    "required": ["state_handoff_id", "status"],
    "properties": {"state_handoff_id": {"type": "string"}, "status": {"type": "string"}},
}
BOUNDS = SuiteBounds(max_iterations=3, budget_ms=1000, handoff_fields=["iteration_pass", "result"])


def passes(count: int, check_ms: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "pass": n,
            "fresh_state": {},
            "action_output": {},
            "check_output": {},
            "action_ms": 5,
            "check_ms": check_ms,
            "ui_update": f"Looping [Pass {n}/3]",
            "gate_eval": {"success": n == count, "escalation": False},
        }
        for n in range(1, count + 1)
    ]


def trace(
    count: int = 1,
    terminal: str | None = "SUCCESS",
    updates: list[str] | None = None,
    **over: Any,
) -> HarnessTrace:
    ui = updates if updates is not None else [f"Looping [Pass {n}/3]" for n in range(1, count + 1)]
    body: dict[str, Any] = {
        "case_id": "c1",
        "terminal": terminal,
        "iterations": count,
        "passes": passes(count, over.pop("check_ms", 10)),
        "ui_updates": [*ui, "Status: Completed"] if terminal else ui,
        "handoff_rows": count,
        **over,
    }
    return HarnessTrace.model_validate(body)


def rows(count: int = 1, **missing: Any) -> list[dict[str, Any]]:
    return [{"iteration_pass": n, "result": "ok", **missing} for n in range(1, count + 1)]


GOOD = {"state_handoff_id": "h-1", "status": "done"}


def context(*types: str, terminal: str = "SUCCESS", value: Any = None) -> Context:
    case = SuiteCase(
        name="c1",
        kind=CaseKind.PASS_FIRST,
        input={},
        expected_terminal=terminal,  # type: ignore[arg-type]
        assertions=[SuiteAssertion(type=AssertionType(t), value=value) for t in types],
    )
    return Context(bounds=BOUNDS, output_schema=SCHEMA, case=case)


def check(kind: str, ev: Evidence, ctx: Context | None = None, value: Any = None) -> Any:
    ctx = ctx or context(kind)
    return run_assertion(SuiteAssertion(type=AssertionType(kind), value=value), ev, ctx)


def test_e13_6_a_clean_success_passes_every_deterministic_assertion() -> None:
    ev = Evidence(trace(), GOOD, rows())
    kinds = [t for t in AssertionType if t is not AssertionType.LLM_RUBRIC]
    kinds = [k for k in kinds if k is not AssertionType.EXPECTED_OUTPUT]
    results = judge_case(context(*(k.value for k in kinds)), ev)
    assert case_passed(results), [r for r in results if not r.passed]


def test_e13_6_t01_output_without_state_handoff_id_fails_by_name() -> None:
    ev = Evidence(trace(), {"status": "done"}, rows())
    result = check("state_handoff_id", ev)
    assert not result.passed and "state_handoff_id" in result.message
    schema = check("output_schema", ev)
    assert not schema.passed and "output_schema" in schema.message


def test_e13_6_t03_iterations_over_the_bound_fail() -> None:
    result = check("iterations_within_bound", Evidence(trace(count=4), GOOD, rows(4)))
    assert not result.passed and "4 passes" in result.message and "3" in result.message
    assert check("iterations_within_bound", Evidence(trace(count=3), GOOD, rows(3))).passed


def test_e13_6_t04_a_check_over_the_budget_fails() -> None:
    result = check("cycle_time_within_budget", Evidence(trace(check_ms=1300), GOOD, rows()))
    assert not result.passed and "1300" in result.message and "1000" in result.message
    assert check("cycle_time_within_budget", Evidence(trace(check_ms=1000), GOOD, rows())).passed


def test_e13_6_t05_a_handoff_row_without_a_required_field_names_the_field() -> None:
    bad = [{"result": "ok"}]  # no iteration_pass
    result = check("handoff_rows", Evidence(trace(), GOOD, bad))
    assert not result.passed and "iteration_pass" in result.message


def test_e13_6_zero_handoff_rows_on_a_success_case_fails() -> None:
    ev = Evidence(trace(handoff_rows=0), GOOD, [])
    result = check("handoff_rows", ev)
    assert not result.passed and "no handoff row" in result.message


def test_e13_6_t06_a_ui_update_that_does_not_match_fails() -> None:
    ev = Evidence(
        trace(count=2, updates=["Looping [Pass 1/3]", "Looping [Pass 2/3]"]), GOOD, rows(2)
    )
    assert check("ui_updates", ev).passed
    expected = ["Looping [Pass 1/3]", "Looping [Pass 2/3]", "Looping [Pass 3/3]"]
    result = check("ui_updates", ev, value=expected)
    assert not result.passed and "Pass 3/3" in result.message


def test_e13_6_ui_updates_out_of_order_fail() -> None:
    swapped = trace(count=2, updates=["Looping [Pass 2/3]", "Looping [Pass 1/3]"])
    result = check("ui_updates", Evidence(swapped, GOOD, rows(2)))
    assert not result.passed


def test_e13_6_a_terminal_case_needs_a_closing_message() -> None:
    silent = trace().model_copy(update={"ui_updates": ["Looping [Pass 1/3]"]})
    result = check("ui_updates", Evidence(silent, GOOD, rows()))
    assert not result.passed and "closing message" in result.message


def test_e13_6_the_terminal_state_must_be_the_expected_one() -> None:
    ev = Evidence(trace(terminal="ESCALATED"), GOOD, rows())
    result = check("terminal_state", ev)
    assert not result.passed and "ESCALATED" in result.message
    assert check("terminal_state", ev, context("terminal_state", terminal="ESCALATED")).passed


def test_e13_6_a_harness_error_is_a_failed_terminal_assertion() -> None:
    error = {
        "category": "TIMEOUT",
        "pass": 1,
        "step": "check",
        "message": "too slow",
        "elapsed_ms": 70,
    }
    failed = trace(terminal=None, error=error)
    result = check("terminal_state", Evidence(failed, None, []))
    assert not result.passed and "TIMEOUT" in result.message


def test_e13_6_expected_output_compares_declared_keys_and_hides_values() -> None:
    ok = check("expected_output", Evidence(trace(), GOOD, rows()), value={"status": "done"})
    assert ok.passed
    bad = check("expected_output", Evidence(trace(), GOOD, rows()), value={"status": "secret-x"})
    assert not bad.passed and "status" in bad.message and "secret-x" not in bad.message


def test_e13_6_t08_a_passing_rubric_cannot_override_a_deterministic_failure() -> None:
    ctx = context("output_schema", "llm_rubric")
    ev = Evidence(trace(), {"status": 7}, rows())  # schema fails
    results = judge_case(ctx, ev, rubric={"c1": True})
    assert not case_passed(results)
    assert [r.type for r in results] == [AssertionType.OUTPUT_SCHEMA]  # the rubric was not even run


def test_e13_6_a_failing_rubric_fails_a_case_that_passed_everything_else() -> None:
    ctx = context("output_schema", "llm_rubric")
    ev = Evidence(trace(), GOOD, rows())
    assert case_passed(judge_case(ctx, ev, rubric={"c1": True}))
    assert not case_passed(judge_case(ctx, ev, rubric={"c1": False}))
    assert not case_passed(judge_case(ctx, ev, rubric=None))  # not graded is not a pass


@pytest.mark.parametrize("count", [0])
def test_e13_6_a_case_with_no_assertions_never_passes(count: int) -> None:
    assert not case_passed([])
