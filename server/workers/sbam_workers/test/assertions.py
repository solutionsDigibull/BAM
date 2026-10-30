"""The deterministic assertions of a Promptfoo suite (E13.6), as plain functions of one case's
evidence.

Evidence is what the sandbox run returned for a case: the harness trace (E13.3), the outputs the
tools gave, and the handoff rows. Nothing here calls a model, the network or `eval`: each function
reads the evidence and answers whether it holds, with a message that names the field that did not
(`state_handoff_id`, `iteration_pass`, `Looping [Pass 2/3]`) and never the values of the outputs.

`judge_case` runs a case's assertions in the suite's order. An `llm_rubric` verdict comes from the
Promptfoo grader and is only ever ANDed in: it can add a failure, never remove one, and it is not
looked at when a deterministic assertion already failed (E13.6-T08).
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from jsonschema import Draft202012Validator
from sbam_contracts.harness import HarnessTrace
from sbam_contracts.promptfoo import (
    AssertionResult,
    AssertionType,
    SuiteAssertion,
    SuiteBounds,
    SuiteCase,
)

from sbam_workers.test.harness import progress_message

HANDOFF_ID: Final = "state_handoff_id"
_PROGRESS: Final = re.compile(r"^Looping \[Pass \d+/\d+\]$")
MAX_NAMES: Final = 5  # how many offending fields a message lists


@dataclass(frozen=True)
class Evidence:
    """One case's run: the trace, the outputs the tools returned, the handoff rows."""

    trace: HarnessTrace
    outputs: Mapping[str, Any] | None = None
    handoff_rows: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True)
class Context:
    """What an assertion needs besides the evidence: the suite's bounds and schema, the case."""

    bounds: SuiteBounds
    output_schema: Mapping[str, Any]
    case: SuiteCase


Verdict = tuple[bool, str]
Check = Callable[[Evidence, Context, Any], Verdict]


def _names(items: Sequence[str]) -> str:
    shown = ", ".join(sorted(items)[:MAX_NAMES])
    return shown + (f" (+{len(items) - MAX_NAMES} more)" if len(items) > MAX_NAMES else "")


def terminal_state(ev: Evidence, ctx: Context, _: Any) -> Verdict:
    if ev.trace.error is not None:
        return False, f"the case failed in the harness: {ev.trace.error.category}"
    expected = ctx.case.expected_terminal
    if ev.trace.terminal != expected:
        return False, f"ended {ev.trace.terminal}, expected {expected}"
    return True, ""


def output_schema(ev: Evidence, ctx: Context, _: Any) -> Verdict:
    if ev.outputs is None:
        return False, "no outputs were returned"
    validator = Draft202012Validator(dict(ctx.output_schema))
    errors = sorted(validator.iter_errors(dict(ev.outputs)), key=str)
    if not errors:
        return True, ""
    paths = ["/" + "/".join(str(p) for p in e.absolute_path) for e in errors]
    return False, f"outputs do not match output_schema at {_names(paths)}"


def state_handoff_id(ev: Evidence, ctx: Context, _: Any) -> Verdict:
    if (ev.outputs or {}).get(HANDOFF_ID) in (None, ""):
        return False, f"{HANDOFF_ID} is missing from the outputs"
    return True, ""


def expected_output(ev: Evidence, ctx: Context, value: Any) -> Verdict:
    if not isinstance(value, Mapping) or not value:
        return False, "the assertion carries no expected outputs"
    have = ev.outputs or {}
    wrong = [key for key, want in value.items() if have.get(key) != want]
    if wrong:
        return False, f"outputs differ from the expected ones at {_names(wrong)}"
    return True, ""


def iterations_within_bound(ev: Evidence, ctx: Context, _: Any) -> Verdict:
    bound = ctx.bounds.max_iterations
    if ev.trace.iterations > bound:
        return False, f"{ev.trace.iterations} passes ran, max_iterations is {bound}"
    return True, ""


def cycle_time_within_budget(ev: Evidence, ctx: Context, _: Any) -> Verdict:
    budget = ctx.bounds.budget_ms
    slow = [
        f"pass {p.pass_no} {step} {ms} ms"
        for p in ev.trace.passes
        for step, ms in (("action", p.action_ms), ("check", p.check_ms))
        if ms > budget
    ]
    if slow:
        return False, f"over budget_limit {budget} ms: {_names(slow)}"
    return True, ""


def handoff_rows(ev: Evidence, ctx: Context, _: Any) -> Verdict:
    rows = ev.handoff_rows
    if ctx.case.expected_terminal == "SUCCESS" and not rows:
        return False, "a SUCCESS case wrote no handoff row"
    if len(rows) != ev.trace.handoff_rows:
        wrote = ev.trace.handoff_rows
        return False, f"{len(rows)} handoff rows returned, the harness wrote {wrote}"
    wanted = ctx.bounds.handoff_fields
    missing = sorted({name for row in rows for name in wanted if row.get(name) in (None, "")})
    if missing:
        return False, f"handoff rows lack {_names(missing)}"
    return True, ""


def ui_updates(ev: Evidence, ctx: Context, value: Any) -> Verdict:
    """The `Looping [Pass p/max]` updates, in order and one per pass (or the sequence the assertion
    names), then a closing message when the case ended in a terminal state."""
    got = [u for u in ev.trace.ui_updates if _PROGRESS.match(u)]
    if isinstance(value, list) and value:
        want = [str(u) for u in value]
    else:
        bound = ctx.bounds.max_iterations
        want = [progress_message(p, bound) for p in range(1, ev.trace.iterations + 1)]
    if got != want:
        first = next((w for w, g in zip(want, got, strict=False) if w != g), None)
        if first is None:
            first = want[len(got)] if len(got) < len(want) else "an extra update"
        return False, f"ui_updates do not match, first difference at {first!r}"
    if ev.trace.terminal is not None and len(ev.trace.ui_updates) <= len(got):
        return False, "no closing message after the last pass"
    return True, ""


CHECKS: Final[Mapping[AssertionType, Check]] = {
    AssertionType.TERMINAL_STATE: terminal_state,
    AssertionType.OUTPUT_SCHEMA: output_schema,
    AssertionType.STATE_HANDOFF_ID: state_handoff_id,
    AssertionType.EXPECTED_OUTPUT: expected_output,
    AssertionType.ITERATIONS_WITHIN_BOUND: iterations_within_bound,
    AssertionType.CYCLE_TIME_WITHIN_BUDGET: cycle_time_within_budget,
    AssertionType.HANDOFF_ROWS: handoff_rows,
    AssertionType.UI_UPDATES: ui_updates,
}


def run_assertion(assertion: SuiteAssertion, ev: Evidence, ctx: Context) -> AssertionResult:
    passed, message = CHECKS[assertion.type](ev, ctx, assertion.value)
    return AssertionResult(case=ctx.case.name, type=assertion.type, passed=passed, message=message)


def judge_case(
    ctx: Context, ev: Evidence, rubric: Mapping[str, bool] | None = None
) -> list[AssertionResult]:
    """Every assertion of the case, in order. `rubric` maps a case name to the grader's verdict."""
    results: list[AssertionResult] = []
    for assertion in ctx.case.assertions:
        if assertion.type is not AssertionType.LLM_RUBRIC:
            results.append(run_assertion(assertion, ev, ctx))
            continue
        if any(not r.passed for r in results):  # a deterministic failure is final
            continue
        verdict = (rubric or {}).get(ctx.case.name)
        message = "" if verdict is True else "the rubric was not met or was not graded"
        results.append(
            AssertionResult(
                case=ctx.case.name,
                type=assertion.type,
                passed=verdict is True,
                message=message,
            )
        )
    return results


def case_passed(results: Sequence[AssertionResult]) -> bool:
    return bool(results) and all(r.passed for r in results)
