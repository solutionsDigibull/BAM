"""E13.6: judging a sandbox run: the verdict, NON_CERTIFYING, and the grader's retry rule."""

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from sbam_contracts.promptfoo import (
    AssertionType,
    Suite,
    SuiteAssertion,
    SuiteBounds,
    SuiteCase,
    TestRunState,
    suite_sha256,
)
from sbam_contracts.sandbox import RunnerOutputs
from sbam_contracts.testgen import CaseKind
from sbam_workers.test.judge import (
    GRADER_ATTEMPTS,
    PromptfooInfraError,
    TestingBlocked,
    judge_run,
    judge_with_grader,
    row_values,
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["state_handoff_id"],
    "properties": {"state_handoff_id": {"type": "string"}},
}
DETERMINISTIC = [AssertionType.TERMINAL_STATE, AssertionType.STATE_HANDOFF_ID]
KINDS = [CaseKind.PASS_FIRST, CaseKind.ESCALATION, CaseKind.NO_OP]
TERMINAL = {
    CaseKind.PASS_FIRST: "SUCCESS",
    CaseKind.ESCALATION: "ESCALATED",
    CaseKind.NO_OP: "NO_OP",
}


def name_of(kind: CaseKind) -> str:
    return kind.value.lower()


def suite(rubric_on: CaseKind | None = None) -> Suite:
    cases = []
    for kind in KINDS:
        assertions = [SuiteAssertion(type=t) for t in DETERMINISTIC]
        if kind is rubric_on:
            assertions.append(SuiteAssertion(type=AssertionType.LLM_RUBRIC, value="reads well"))
        cases.append(
            SuiteCase(
                name=name_of(kind),
                kind=kind,
                input={},
                expected_terminal=TERMINAL[kind],  # type: ignore[arg-type]
                assertions=assertions,
            )
        )
    return Suite(
        bounds=SuiteBounds(max_iterations=3, budget_ms=1000, handoff_fields=[]),
        output_schema=SCHEMA,
        cases=cases,
    )


def raw_case(kind: CaseKind, handoff: str | None = "h-1") -> dict[str, Any]:
    terminal = TERMINAL[kind]
    check = {"state_handoff_id": handoff} if handoff else {}
    return {
        "case_id": name_of(kind),
        "expected_terminal": terminal,
        "terminal": terminal,
        "passed": True,
        "trace": {
            "case_id": name_of(kind),
            "terminal": terminal,
            "iterations": 1,
            "passes": [
                {
                    "pass": 1,
                    "fresh_state": {},
                    "action_output": {},
                    "check_output": check,
                    "action_ms": 1,
                    "check_ms": 1,
                    "ui_update": "Looping [Pass 1/3]",
                    "gate_eval": {"success": True, "escalation": False},
                }
            ],
            "ui_updates": ["Looping [Pass 1/3]", "Status: Completed"],
            "handoff_rows": 1,
        },
        "handoff_rows": [{"iteration_pass": 1}],
    }


def outputs(
    mode: str = "CUBESANDBOX", bad: CaseKind | None = None, skip: CaseKind | None = None
) -> RunnerOutputs:
    cases = [raw_case(k, handoff=None if k is bad else "h-1") for k in KINDS if k is not skip]
    return RunnerOutputs.model_validate(
        {
            "runtime_digest": "sha256:" + "a" * 64,
            "cases": cases,
            "resources": {"wall_ms": 1, "cpu_ms": 1, "peak_rss_kb": 1},
            "mode": mode,
        }
    )


def test_e13_6_t02_three_of_three_passes() -> None:
    result = judge_run(suite(), outputs())
    assert (result.state, result.total, result.passed, result.failed) == (
        TestRunState.PASS,
        3,
        3,
        0,
    )
    assert not result.non_certifying and result.green


def test_e13_6_t01_a_known_bad_tool_fails_naming_the_field() -> None:
    result = judge_run(suite(), outputs(bad=CaseKind.PASS_FIRST))
    failures = [r for r in result.results if not r.passed]
    assert result.state is TestRunState.FAIL and result.failed == 1 and not result.green
    assert any(r.type is AssertionType.STATE_HANDOFF_ID for r in failures)
    assert "state_handoff_id" in failures[0].message


def test_e13_6_a_case_the_sandbox_did_not_return_fails_the_run() -> None:
    result = judge_run(suite(), outputs(skip=CaseKind.NO_OP))
    assert result.state is TestRunState.FAIL and result.passed == 2 and result.failed == 1


def test_e13_6_a_green_run_from_a_non_certifying_sandbox_never_reads_as_pass() -> None:
    result = judge_run(suite(), outputs(mode="LOCAL_DOCKER_NONCERT"))
    assert result.state is TestRunState.NON_CERTIFYING and result.non_certifying
    assert result.passed == 3 and result.failed == 0


def test_e13_6_a_failing_run_from_a_non_certifying_sandbox_is_still_a_fail() -> None:
    result = judge_run(suite(), outputs(mode="LOCAL_DOCKER_NONCERT", bad=CaseKind.NO_OP))
    assert result.state is TestRunState.FAIL and result.non_certifying


# --- the grader ----------------------------------------------------------------------------------


class Grader:
    def __init__(self, fail_times: int = 0, verdict: bool = True) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.verdict = verdict

    async def __call__(self, cases: Sequence[SuiteCase], _: RunnerOutputs) -> Mapping[str, bool]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise PromptfooInfraError("exit 1, no results file")
        return {c.name: self.verdict for c in cases}


def judged(rubric_on: CaseKind | None, out: RunnerOutputs, grader: Grader) -> Any:
    return asyncio.run(judge_with_grader(suite(rubric_on), out, grader))


def test_e13_6_t07_a_grader_that_never_answers_blocks_testing_after_three_attempts() -> None:
    grader = Grader(fail_times=99)
    with pytest.raises(TestingBlocked):
        judged(CaseKind.PASS_FIRST, outputs(), grader)
    assert grader.calls == GRADER_ATTEMPTS == 3


def test_e13_6_a_grader_that_recovers_gives_its_verdict() -> None:
    grader = Grader(fail_times=2)
    result = judged(CaseKind.PASS_FIRST, outputs(), grader)
    assert grader.calls == 3 and result.state is TestRunState.PASS


def test_e13_6_t08_a_rubric_the_grader_fails_fails_the_case() -> None:
    result = judged(CaseKind.PASS_FIRST, outputs(), Grader(verdict=False))
    assert result.state is TestRunState.FAIL and result.failed == 1


def test_e13_6_the_grader_is_not_asked_when_no_case_has_a_rubric() -> None:
    grader = Grader()
    assert judged(None, outputs(), grader).state is TestRunState.PASS
    assert grader.calls == 0


def test_e13_6_the_grader_is_not_asked_about_a_case_that_already_failed() -> None:
    grader = Grader()
    result = judged(CaseKind.PASS_FIRST, outputs(bad=CaseKind.PASS_FIRST), grader)
    assert result.state is TestRunState.FAIL and grader.calls == 0


def test_e13_6_the_row_carries_the_suite_hash_and_the_verdict() -> None:
    built = suite()
    result = judge_run(built, outputs(mode="LOCAL_DOCKER_NONCERT"))
    values = row_values(
        sub_skill_id=uuid.uuid4(),
        code_attempt_id=uuid.uuid4(),
        sandbox_run_id=uuid.uuid4(),
        suite=built,
        judgement=result,
    )
    assert values["suite_sha256"] == suite_sha256(built)
    assert (values["state"], values["total"], values["passed"], values["failed"]) == (
        "NON_CERTIFYING",
        3,
        3,
        0,
    )
    assert values["non_certifying"] is True and len(values["results"]) == 6
