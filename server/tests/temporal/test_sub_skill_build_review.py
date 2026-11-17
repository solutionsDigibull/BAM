"""#759 (WG-11) SubSkillBuild with the review pass: one model review after the AST and static gates,
before the tests are generated. Real child workflow on Temporal's time-skipping server, stub stages
overridden per test (see test_sub_skill_build.py for the harness).

- WG11-T01: a failed gate never reaches the review.
- WG11-T02: a failed review goes to the corrector like a gate; with no correction left the Sub-Skill
  waits in NEEDS_FDE_REVIEW and is never certified.
- WG11-T04: a budget refusal (or any model failure) in the review parks the build, never crashes it.
- Replay: histories recorded before the review pass keep replaying (it is behind
  `workflow.patched`); a build that ran it replays from its own history.
"""

import asyncio
import base64
import json
import os
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sbam_contracts.build import BuildStage, BuildStageInput, CorrectionInput
from sbam_contracts.runs import build_workflow_id
from sbam_workers.pipeline.sub_skill_build import MODEL_STAGES, SPECS, SubSkillBuild
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer
from test_sub_skill_build import (
    FINDING,
    HISTORY,
    Build,
    gate_failed,
    run,
    save_history,
    traced,
)

REVIEW_HISTORY = HISTORY.with_name("sub_skill_build_review.json")
TESTS_AND_AFTER = [
    "generate_tests",
    "build_promptfoo_suite",
    "run_sandbox_tests",
    "certify_sub_skill",
]


def review_fails_on(attempts: set[int]) -> Callable[[BuildStageInput], None]:
    """The review answers FAIL (GateFailed, as the real stage raises it) on these attempts."""

    def behave(inp: BuildStageInput) -> None:
        if inp.cycle == 0 and inp.attempt in attempts:
            gate_failed()

    return behave


def patch_ids(history: dict[str, Any]) -> set[str]:
    """The `workflow.patched` ids a history recorded (the marker's details are base64 JSON)."""
    ids: set[str] = set()
    for event in history["events"]:
        if event.get("eventType") == "EVENT_TYPE_MARKER_RECORDED":
            details = event["markerRecordedEventAttributes"].get("details", {})
            for payloads in details.values():
                for payload in payloads["payloads"]:
                    ids.add(json.loads(base64.b64decode(payload["data"]))["id"])
    return ids


def scheduled(history: dict[str, Any], name: str) -> int:
    return sum(
        1
        for e in history["events"]
        if e.get("eventType") == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
        and e["activityTaskScheduledEventAttributes"]["activityType"]["name"] == name
    )


def model_failure(kind: str, *, retryable: bool = False) -> ApplicationError:
    return ApplicationError(f"{kind} (test)", type=kind, non_retryable=not retryable)


def notice(build: Build, action: str) -> dict[str, Any]:
    [row] = [r for r in build.audit() if r.action == action]
    assert row.after is not None
    after: dict[str, Any] = row.after
    return after


def test_wg11_the_review_stage_is_a_model_stage_on_the_llm_queue() -> None:
    spec = SPECS[BuildStage.REVIEW]
    assert BuildStage.REVIEW in MODEL_STAGES
    assert (spec.queue.value, spec.attempts) == ("sbam-llm", 3)
    assert set(spec.non_retryable) == {"GateFailed", "SchemaInvalidAfterRetries"}
    assert (
        "BudgetExceeded" not in spec.non_retryable
    )  # parity with generate_bodies / generate_tests


# --- the happy path ------------------------------------------------------------------------------


def test_wg11_the_review_runs_once_after_the_gates_and_before_the_tests() -> None:
    order: list[str] = []

    def recorder(name: str) -> Callable[[Any], None]:
        return lambda inp: order.append(name)

    names = ["ast_gate", "static_gate", "review_code", *TESTS_AND_AFTER]
    overrides = {name: traced(name, [], recorder(name)) for name in names}

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.actions(), build.state()

    result, actions, state = run(scenario, **overrides)
    assert order == names  # review sits between static_gate and generate_tests, once
    assert result.certified and state == "CERTIFIED"
    assert actions == ["tests_started", "tests_passed", "sub_skill.certified"]


# --- WG11-T01 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("gate", ["ast_gate", "static_gate"])
def test_wg11_t01_a_failed_gate_never_reaches_the_review(gate: str) -> None:
    reviews: list[BuildStageInput] = []

    def gate_fails_first_pass(inp: BuildStageInput) -> None:
        if inp.attempt == 0:
            gate_failed()

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.actions()

    result, actions = run(
        scenario,
        **{
            gate: traced(gate, [], gate_fails_first_pass),
            "review_code": traced("review_code", reviews),
        },
    )
    assert result.certified
    assert [r.attempt for r in reviews] == [1]  # attempt 0 failed its gate before the review
    assert actions[:2] == ["gate_failed", "correction_applied"]


def test_wg11_t01_a_gate_that_keeps_failing_never_asks_the_reviewer() -> None:
    reviews: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        await build.end()
        return waiting

    waiting = run(
        scenario,
        ast_gate=traced("ast_gate", [], gate_failed),
        review_code=traced("review_code", reviews),
    )
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.corrections_used == 2
    assert reviews == []


# --- WG11-T02 ------------------------------------------------------------------------------------


def test_wg11_t02_a_failed_review_goes_to_the_corrector_and_the_next_pass_is_reviewed_again() -> (
    None
):
    reviews: list[BuildStageInput] = []
    corrections: list[CorrectionInput] = []
    tests: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.actions(), build.state()

    result, actions, state = run(
        scenario,
        review_code=traced("review_code", reviews, review_fails_on({0})),
        correct=traced("correct", corrections),
        generate_tests=traced("generate_tests", tests),
    )
    assert [r.attempt for r in reviews] == [0, 1]
    [correction] = corrections
    assert (correction.attempt, correction.failed_gate) == (0, "review_code")
    assert correction.finding_ids == [FINDING]  # the same id shape every gate gives
    assert [t.attempt for t in tests] == [1]  # attempt 0 never got as far as its tests
    assert actions == [
        "gate_failed",
        "correction_applied",
        "tests_started",
        "tests_passed",
        "sub_skill.certified",
    ]
    assert result.certified and state == "CERTIFIED"


def test_wg11_t02_a_review_that_keeps_failing_waits_for_the_fde_and_never_certifies() -> None:
    reviews: list[BuildStageInput] = []
    certified: list[BuildStageInput] = []
    tests: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        said = notice(build, "sub_skill.needs_fde_review")
        state, before = build.state(), build.actions()
        await build.end()
        return waiting, said, state, before

    waiting, said, state, before = run(
        scenario,
        review_code=traced("review_code", reviews, review_fails_on({0, 1, 2})),
        certify_sub_skill=traced("certify_sub_skill", certified),
        generate_tests=traced("generate_tests", tests),
    )
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.corrections_used == 2
    assert [r.attempt for r in reviews] == [0, 1, 2]  # the first pass and both corrections
    assert certified == [] and tests == []  # nothing past the review ever ran
    assert state == "NEEDS_FDE_REVIEW"
    assert before == [
        "gate_failed",
        "correction_applied",
        "gate_failed",
        "correction_applied",
        "gate_failed",
        "attempts_exhausted",
        "sub_skill.needs_fde_review",
    ]
    assert said["finding_ids"] == str(FINDING) and said["next_action"] == "retry"


# --- WG11-T04 ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "retryable", "calls"),
    [
        (
            "BudgetExceeded",
            True,
            3,
        ),  # not listed non-retryable: three cheap attempts, then it parks
        ("SchemaInvalidAfterRetries", False, 1),
        ("LlmTransportError", True, 3),
    ],
)
def test_wg11_t04_a_model_failure_in_the_review_parks_the_build_and_a_retry_certifies(
    kind: str, retryable: bool, calls: int
) -> None:
    flag = {"on": True}
    reviews: list[BuildStageInput] = []
    corrections: list[CorrectionInput] = []
    tests: list[BuildStageInput] = []

    def failing(inp: BuildStageInput) -> None:
        if flag["on"]:
            raise model_failure(kind, retryable=retryable)

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        said = notice(build, "sub_skill.generation_error")
        parked, before = build.state(), build.actions()
        flag["on"] = False
        await build.retry()
        return waiting, said, parked, before, await build.finish(), build.state()

    waiting, said, parked, before, result, state = run(
        scenario,
        review_code=traced("review_code", reviews, failing),
        correct=traced("correct", corrections),
        generate_tests=traced("generate_tests", tests),
    )
    assert parked == "NEEDS_FDE_REVIEW" and waiting.waiting_for_retry  # parked, not crashed
    assert waiting.corrections_used == 0 and corrections == []  # a model failure is no correction
    assert before == ["generation_failed", "sub_skill.generation_error"]
    assert said["error"] == kind and said["stage"] == "review"  # the type only, never the message
    assert "(test)" not in json.dumps(said) and said["next_action"] == "retry"
    assert len([r for r in reviews if r.cycle == 0]) == calls
    assert [t.cycle for t in tests] == [1]  # the build only went on after the retry's new cycle
    assert result.certified and state == "CERTIFIED"


# --- replay --------------------------------------------------------------------------------------


def test_wg11_histories_recorded_before_the_review_pass_replay_unchanged() -> None:
    """The review is behind `workflow.patched("wg11-reviewer-pass")`: a history that has no marker
    and no `review_code` event (every one recorded before #759) replays on the new code."""
    raw = json.loads(HISTORY.read_text("utf-8"))
    assert "wg11-reviewer-pass" not in patch_ids(raw) and scheduled(raw, "review_code") == 0
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


def test_wg11_replays_the_recorded_history_of_a_build_that_ran_the_review() -> None:
    """A build whose review failed once, was corrected, passed the review and certified."""
    raw = json.loads(REVIEW_HISTORY.read_text("utf-8"))
    assert "wg11-reviewer-pass" in patch_ids(raw) and scheduled(raw, "review_code") == 2
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_review_history() -> None:  # pragma: no cover - maintenance helper
    """#759: the review fails the first pass, the corrector fixes it, the second pass certifies."""

    async def scenario(build: Build) -> dict[str, Any]:
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    data = run(scenario, review_code=traced("review_code", [], review_fails_on({0})))
    save_history(REVIEW_HISTORY, data)
