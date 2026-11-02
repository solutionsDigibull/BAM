"""Judging a sandbox run (E13.6): the suite's assertions over what the sandbox returned, and the
`test_runs` row that records the verdict.

`judge_run` is a pure function of the suite, the runner's `outputs.json` and the sandbox mode:
every case of the suite must have come back, every assertion of every case must hold (no
percentage threshold), and a run from a non-certifying sandbox (`LOCAL_DOCKER_NONCERT`) is valid
evidence that never certifies, so its green result is `NON_CERTIFYING`, never `PASS`.

The Promptfoo grader is the only part that can fail for reasons that are not the code's. A crash or
a non-zero exit with no results is `PromptfooInfraError`: `grade_with_retries` asks again up to
`GRADER_ATTEMPTS` times and then raises `TestingBlocked`, so the Sub-Skill is parked in
TESTING_BLOCKED and no correction is spent (E13.6-T07). A rubric verdict is only ever ANDed in
(`assertions.judge_case`).

Messages name fields and counts, never the values of outputs.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sbam_api.db.repository import TenantContext
from sbam_api.testruns.repository import TestRunRepository
from sbam_contracts.harness import HarnessTrace
from sbam_contracts.promptfoo import (
    AssertionResult,
    AssertionType,
    Suite,
    SuiteCase,
    TestRunState,
    suite_sha256,
)
from sbam_contracts.sandbox import RunnerCaseResult, RunnerOutputs
from sqlalchemy.orm import Session, sessionmaker

from sbam_workers.test.assertions import Context, Evidence, case_passed, judge_case

GRADER_ATTEMPTS: Final = 3  # E13.6-T07: retried up to three times, then TESTING_BLOCKED
MISSING: Final = "the sandbox returned nothing for this case"


class PromptfooInfraError(Exception):
    """Promptfoo itself failed (crash, OOM, non-zero exit without results): not the code's fault."""


class TestingBlocked(Exception):  # noqa: N818 - the state it leads to is TESTING_BLOCKED
    """The grader stayed down for every attempt: park the Sub-Skill, spend no correction."""

    __test__ = False


@dataclass(frozen=True)
class Judgement:
    state: TestRunState
    total: int
    passed: int
    failed: int
    non_certifying: bool
    results: tuple[AssertionResult, ...]

    @property
    def green(self) -> bool:
        return self.failed == 0 and self.total > 0


def outputs_of(trace: HarnessTrace) -> dict[str, Any] | None:
    """What the tools gave on the last pass: the action's outputs with the check's over them."""
    if not trace.passes:
        return None
    last = trace.passes[-1]
    return {**last.action_output, **last.check_output}


def evidence_of(raw: RunnerCaseResult) -> Evidence:
    trace = HarnessTrace.model_validate(raw.trace)
    return Evidence(trace=trace, outputs=outputs_of(trace), handoff_rows=tuple(raw.handoff_rows))


def needs_rubric(suite: Suite) -> list[SuiteCase]:
    return [
        case
        for case in suite.cases
        if any(a.type is AssertionType.LLM_RUBRIC for a in case.assertions)
    ]


def judge_run(
    suite: Suite,
    outputs: RunnerOutputs,
    rubric: Mapping[str, bool] | None = None,
) -> Judgement:
    """The verdict on every case of `suite`. A case the sandbox did not return fails; a case the
    suite does not know is ignored (it cannot make a run pass)."""
    returned = {case.case_id: case for case in outputs.cases}
    results: list[AssertionResult] = []
    passed = 0
    for case in suite.cases:
        raw = returned.get(case.name)
        if raw is None:
            case_results = [
                AssertionResult(
                    case=case.name,
                    type=AssertionType.TERMINAL_STATE,
                    passed=False,
                    message=MISSING,
                )
            ]
        else:
            ctx = Context(bounds=suite.bounds, output_schema=suite.output_schema, case=case)
            case_results = judge_case(ctx, evidence_of(raw), rubric)
        results += case_results
        passed += case_passed(case_results)
    total = len(suite.cases)
    non_certifying = not outputs.mode.certifying
    state = TestRunState.FAIL
    if passed == total:
        state = TestRunState.NON_CERTIFYING if non_certifying else TestRunState.PASS
    return Judgement(
        state=state,
        total=total,
        passed=passed,
        failed=total - passed,
        non_certifying=non_certifying,
        results=tuple(results),
    )


Grader = Callable[[Sequence[SuiteCase], RunnerOutputs], Awaitable[Mapping[str, bool]]]


async def grade_with_retries(
    grader: Grader,
    cases: Sequence[SuiteCase],
    outputs: RunnerOutputs,
    attempts: int = GRADER_ATTEMPTS,
) -> Mapping[str, bool]:
    """The rubric verdicts by case name. No case with a rubric: the grader is not called."""
    if not cases:
        return {}
    for _ in range(attempts):
        try:
            return await grader(cases, outputs)
        except PromptfooInfraError:
            continue
    raise TestingBlocked(f"the Promptfoo grader failed {attempts} times")


async def judge_with_grader(suite: Suite, outputs: RunnerOutputs, grader: Grader) -> Judgement:
    """Deterministic assertions need no grader; the grader is asked only about cases that passed
    them (a deterministic failure is final, so its rubric is never graded)."""
    first = judge_run(suite, outputs)
    wanted = [
        case
        for case in needs_rubric(suite)
        if all(
            r.passed
            for r in first.results
            if r.case == case.name and r.type is not AssertionType.LLM_RUBRIC
        )
    ]
    rubric = await grade_with_retries(grader, wanted, outputs)
    return judge_run(suite, outputs, rubric)


def row_values(
    *,
    sub_skill_id: uuid.UUID,
    code_attempt_id: uuid.UUID,
    sandbox_run_id: uuid.UUID,
    suite: Suite,
    judgement: Judgement,
    report_key: str | None = None,
    created_by: str = "worker-build",
) -> dict[str, Any]:
    """The columns of a `test_runs` row; the repository stamps the tenant and project."""
    return {
        "sub_skill_id": sub_skill_id,
        "code_attempt_id": code_attempt_id,
        "sandbox_run_id": sandbox_run_id,
        "suite_sha256": suite_sha256(suite),
        "state": judgement.state.value,
        "total": judgement.total,
        "passed": judgement.passed,
        "failed": judgement.failed,
        "non_certifying": judgement.non_certifying,
        "results": [r.model_dump(mode="json") for r in judgement.results],
        "report_key": report_key,
        "created_by": created_by,
    }


async def record_run(
    sessions: sessionmaker[Session], ctx: TenantContext, values: dict[str, Any]
) -> uuid.UUID:
    """Insert the run (a retried activity finds the row an earlier attempt wrote)."""

    def write() -> uuid.UUID:
        with sessions.begin() as session:
            return TestRunRepository(session, ctx).record(values).id

    return await asyncio.to_thread(write)
