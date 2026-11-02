"""E13.6: the judging step of the sandbox stage: the suite can fail a run the classifier passed, a
rubric with no grader blocks testing, and the test_runs row is written once (no sandbox, no model).
"""

import asyncio
import uuid
from typing import Any

import pytest
from sbam_contracts.build import BuildStageInput
from sbam_contracts.promptfoo import AssertionType, TestRunState
from sbam_contracts.sandbox import FailureCategory, InfraReason
from sbam_contracts.testgen import CaseKind
from sbam_workers.sandbox import judging
from sbam_workers.sandbox.classify import CaseVerdict, Classified
from sbam_workers.sandbox.judging import JudgeBlocked, StageJudge
from sbam_workers.sandbox.load import Identity
from test_e13_6_judge import Grader, outputs, suite

PASSED = Classified(None, None, (CaseVerdict("x", True, None),))
FAILED = Classified(FailureCategory.ASSERTION_FAILED, None, (CaseVerdict("x", False, None, "no"),))
IDENTITY = Identity(uuid.uuid4(), uuid.uuid4(), "sha256:" + "a" * 64)


class Recorded:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    recorded = Recorded()

    async def record(_sessions: Any, _ctx: Any, values: dict[str, Any]) -> uuid.UUID:
        recorded.rows.append(values)
        return uuid.uuid4()

    monkeypatch.setattr(judging, "record_run", record)
    return recorded


def judge_over(
    monkeypatch: pytest.MonkeyPatch, rubric_on: CaseKind | None, grader: Grader | None = None
) -> StageJudge:
    built = suite(rubric_on)
    monkeypatch.setattr(judging, "load_suite", lambda *_: (built, uuid.uuid4()))

    async def isatvon_of(_: BuildStageInput) -> dict[str, Any]:
        return {}

    return StageJudge(None, isatvon_of, grader)  # type: ignore[arg-type]


def inp() -> BuildStageInput:
    return BuildStageInput.model_construct(
        tenant_id=uuid.uuid4(), project_id=uuid.uuid4(), sub_skill_id=uuid.uuid4()
    )


def run(judge: StageJudge, out: Any, classified: Classified) -> Classified:
    return asyncio.run(judge.judge(inp(), IDENTITY, uuid.uuid4(), out, classified))


def test_e13_6_a_green_suite_returns_the_classification_and_records_the_run(
    monkeypatch: pytest.MonkeyPatch, rec: Recorded
) -> None:
    result = run(judge_over(monkeypatch, None), outputs(), PASSED)
    assert result is PASSED
    assert [r["state"] for r in rec.rows] == [TestRunState.PASS.value]
    assert rec.rows[0]["total"] == 3 and len(rec.rows[0]["suite_sha256"]) == 64


def test_e13_6_the_suite_fails_a_run_the_classifier_passed_with_findings_for_the_corrector(
    monkeypatch: pytest.MonkeyPatch, rec: Recorded
) -> None:
    result = run(judge_over(monkeypatch, None), outputs(bad=CaseKind.PASS_FIRST), PASSED)
    assert result.category is FailureCategory.ASSERTION_FAILED and not result.passed
    [verdict] = result.cases
    assert verdict.case_id == "pass_first" and "state_handoff_id" in (verdict.message or "")
    assert [f.code for f in result.findings()] == ["ASSERTION_FAILED"]
    assert rec.rows[0]["state"] == TestRunState.FAIL.value and rec.rows[0]["failed"] == 1


def test_e13_6_a_run_the_classifier_failed_keeps_its_classification_but_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch, rec: Recorded
) -> None:
    result = run(judge_over(monkeypatch, None), outputs(bad=CaseKind.NO_OP), FAILED)
    assert result is FAILED and len(rec.rows) == 1


def test_e13_6_a_non_certifying_run_is_recorded_as_such_and_still_passes_the_stage(
    monkeypatch: pytest.MonkeyPatch, rec: Recorded
) -> None:
    result = run(judge_over(monkeypatch, None), outputs(mode="LOCAL_DOCKER_NONCERT"), PASSED)
    assert result is PASSED
    assert rec.rows[0]["state"] == "NON_CERTIFYING" and rec.rows[0]["non_certifying"] is True


def test_e13_6_a_rubric_with_no_grader_blocks_testing_and_writes_no_row(
    monkeypatch: pytest.MonkeyPatch, rec: Recorded
) -> None:
    with pytest.raises(JudgeBlocked) as blocked:
        run(judge_over(monkeypatch, CaseKind.PASS_FIRST), outputs(), PASSED)
    assert blocked.value.reason is InfraReason.NOT_CONFIGURED and rec.rows == []


def test_e13_6_t07_a_grader_that_stays_down_blocks_testing_and_writes_no_row(
    monkeypatch: pytest.MonkeyPatch, rec: Recorded
) -> None:
    grader = Grader(fail_times=99)
    with pytest.raises(JudgeBlocked) as blocked:
        run(judge_over(monkeypatch, CaseKind.PASS_FIRST, grader), outputs(), PASSED)
    assert blocked.value.reason is InfraReason.RUNNER_FAILED
    assert grader.calls == 3 and rec.rows == []


def test_e13_6_a_graded_rubric_passes_through_to_the_row(
    monkeypatch: pytest.MonkeyPatch, rec: Recorded
) -> None:
    result = run(judge_over(monkeypatch, CaseKind.PASS_FIRST, Grader()), outputs(), PASSED)
    assert result is PASSED and rec.rows[0]["state"] == "PASS"
    assert AssertionType.LLM_RUBRIC.value in {r["type"] for r in rec.rows[0]["results"]}
