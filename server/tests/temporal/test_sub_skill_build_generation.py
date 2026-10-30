"""#483 SubSkillBuild when the model cannot answer, and the escalation's findings (E11.2-T03,
E11.2-T06, E11.4-T02). Real child workflow on Temporal's time-skipping server, stub stage activities
overridden per test (see test_sub_skill_build.py for the harness)."""

import asyncio
import json
import os
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sbam_contracts.build import BuildStageInput
from sbam_contracts.runs import build_workflow_id
from sbam_workers.pipeline.sub_skill_build import (
    TIMED_OUT,
    SubSkillBuild,
    _model_failure,
)
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError, TimeoutError, TimeoutType
from temporalio.worker import Replayer
from test_sub_skill_build import HISTORY, Build, run, save_history, traced

GENERATION_FAILED_HISTORY = HISTORY.with_name("sub_skill_build_generation_failed.json")


def model_failure(kind: str, *, retryable: bool = False) -> ApplicationError:
    return ApplicationError(f"{kind} (test)", type=kind, non_retryable=not retryable)


def failing_while(
    flag: dict[str, bool], error: Callable[[], ApplicationError]
) -> Callable[[Any], None]:
    def behave(inp: Any) -> None:
        if flag["on"]:
            raise error()

    return behave


def notice(build: Build, action: str) -> dict[str, Any]:
    [row] = [r for r in build.audit() if r.action == action]
    assert row.after is not None
    after: dict[str, Any] = row.after
    return after


# --- E11.2-T03: the model's re-asks are spent -----------------------------------------------------


def test_e11_2_t03_exhausted_reasks_wait_in_review_with_the_cause_then_retry_certifies() -> None:
    flag = {"on": True}
    bodies: list[BuildStageInput] = []
    corrections: list[Any] = []

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        before = build.actions()
        said = notice(build, "sub_skill.generation_error")
        flag["on"] = False
        await build.retry()
        return waiting, before, said, await build.finish(), build.state()

    waiting, before, said, result, state = run(
        scenario,
        generate_bodies=traced(
            "generate_bodies",
            bodies,
            failing_while(flag, lambda: model_failure("SchemaInvalidAfterRetries")),
        ),
        correct=traced("correct", corrections),
    )
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.waiting_for_retry
    assert (waiting.cycle, waiting.attempt, waiting.corrections_used) == (0, 0, 0)
    assert waiting.finding_ids == []  # nothing to review in the code: the model did not answer
    assert before == ["generation_failed", "sub_skill.generation_error"]
    assert said["error"] == "SchemaInvalidAfterRetries"  # the type only, never the message
    assert said["stage"] == "body generation" and said["next_action"] == "retry"
    assert "(test)" not in json.dumps(said)
    assert [(c.cycle, c.attempt) for c in bodies] == [(0, 0), (1, 0)]  # non-retryable: one call
    assert corrections == []  # a model failure is not a correction
    assert result.certified and state == "CERTIFIED"


# --- E11.2-T06: the transport retries are spent ---------------------------------------------------


def test_e11_2_t06_exhausted_transport_retries_wait_in_review_without_corrections() -> None:
    flag = {"on": True}
    bodies: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        said = notice(build, "sub_skill.generation_error")
        flag["on"] = False
        await build.retry()
        return waiting, said, await build.finish()

    waiting, said, result = run(
        scenario,
        generate_bodies=traced(
            "generate_bodies",
            bodies,
            failing_while(flag, lambda: model_failure("LlmTransportError", retryable=True)),
        ),
    )
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.corrections_used == 0
    assert said["error"] == "LlmTransportError" and said["next_action"] == "retry"
    first_cycle = [c for c in bodies if c.cycle == 0]
    assert len(first_cycle) == 3  # Temporal's three attempts, then the Sub-Skill waits
    assert result.certified


def test_e11_2c_the_test_generation_stage_is_handled_the_same_way() -> None:
    flag = {"on": True}

    async def scenario(build: Build) -> Any:
        await build.until_waiting()
        said = notice(build, "sub_skill.generation_error")
        flag["on"] = False
        await build.retry()
        return said, await build.finish()

    said, result = run(
        scenario,
        generate_tests=traced(
            "generate_tests",
            [],
            failing_while(flag, lambda: model_failure("SchemaInvalidAfterRetries")),
        ),
    )
    assert said["stage"] == "test generation" and said["error"] == "SchemaInvalidAfterRetries"
    assert result.certified


def test_e11_4b_edited_tests_that_no_longer_fit_wait_in_review_with_the_reason() -> None:
    flag = {"on": True}

    async def scenario(build: Build) -> Any:
        await build.until_waiting()
        said = notice(build, "sub_skill.generation_error")
        flag["on"] = False
        await build.retry()
        return said, await build.finish()

    said, result = run(
        scenario,
        generate_tests=traced(
            "generate_tests",
            [],
            failing_while(flag, lambda: model_failure("TestDataStale")),
        ),
    )
    assert said["stage"] == "test generation" and said["error"] == "TestDataStale"
    assert said["next_action"] == "retry" and result.certified


def test_e11_2c_a_platform_defect_still_fails_the_workflow_loudly() -> None:
    """A wiring mismatch is not the model's fault and an FDE retry would not fix it."""

    async def scenario(build: Build) -> Any:
        with pytest.raises(WorkflowFailureError):
            await build.finish()
        return build.actions(), build.state()

    actions, state = run(
        scenario,
        generate_bodies=traced(
            "generate_bodies",
            [],
            failing_while({"on": True}, lambda: model_failure("InputMismatch")),
        ),
    )
    assert actions == [] and state == "BUILDING"  # nothing was parked, nobody was told


@pytest.mark.parametrize(
    ("cause", "expected"),
    [
        (model_failure("SchemaInvalidAfterRetries"), "SchemaInvalidAfterRetries"),
        (model_failure("LlmTransportError"), "LlmTransportError"),
        (model_failure("LlmRequestRejected"), "LlmRequestRejected"),
        (model_failure("BudgetExceeded"), "BudgetExceeded"),
        (model_failure("ContextTooLarge"), "ContextTooLarge"),
        (model_failure("TestDataStale"), "TestDataStale"),
        (
            TimeoutError("late", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[]),
            TIMED_OUT,
        ),
        (model_failure("InputMismatch"), None),  # a wiring bug
        (model_failure("UnknownAlias"), None),  # a deployment defect
        (model_failure("PromptHashMismatch"), None),
        (ValueError("not even an application error"), None),
        (None, None),
    ],
)
def test_e11_2c_only_the_model_side_failures_are_recognised(
    cause: BaseException | None, expected: str | None
) -> None:
    assert _model_failure(cause) == expected


# --- E11.4-T02: the escalation lists every attempt's findings -------------------------------------


def test_e11_4_t02_the_escalation_lists_the_findings_of_attempts_zero_to_two() -> None:
    ids = [uuid.uuid4() for _ in range(3)]

    def behave(inp: BuildStageInput) -> None:
        raise ApplicationError(
            "gate failed", [str(ids[inp.attempt])], type="GateFailed", non_retryable=True
        )

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        said = notice(build, "sub_skill.needs_fde_review")
        await build.end()
        return waiting, said

    waiting, said = run(scenario, ast_gate=traced("ast_gate", [], behave))
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.corrections_used == 2
    assert waiting.finding_ids == ids  # what the FDE is told to look at: all three passes
    assert said["finding_ids"] == ",".join(str(i) for i in ids)


def test_e11_4_t02_a_finding_repeated_by_every_attempt_is_listed_once() -> None:
    async def scenario(build: Build) -> Any:
        await build.until_waiting()
        said = notice(build, "sub_skill.needs_fde_review")
        await build.end()
        return said

    from test_sub_skill_build import FINDING, gate_failed

    said = run(scenario, ast_gate=traced("ast_gate", [], gate_failed))
    assert said["finding_ids"] == str(FINDING)


# --- replay ---------------------------------------------------------------------------------------


def test_e11_2c_replays_the_recorded_generation_failed_history() -> None:
    """A build that parked on a model failure and certified after the FDE's retry keeps replaying
    (the change is behind workflow.patched; histories recorded before it replay in
    test_replay_histories)."""
    workflow_id = build_workflow_id(uuid.UUID(int=0), 1)
    history = WorkflowHistory.from_json(
        workflow_id, json.loads(GENERATION_FAILED_HISTORY.read_text("utf-8"))
    )
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_generation_failed_history() -> None:  # pragma: no cover - maintenance helper
    """#483: the model cannot answer (NEEDS_FDE_REVIEW), the FDE retries, the build certifies."""
    flag = {"on": True}

    async def scenario(build: Build) -> dict[str, Any]:
        await build.until_waiting()
        flag["on"] = False
        await build.retry()
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    bodies = traced(
        "generate_bodies",
        [],
        failing_while(flag, lambda: model_failure("SchemaInvalidAfterRetries")),
    )
    save_history(GENERATION_FAILED_HISTORY, run(scenario, generate_bodies=bodies))
