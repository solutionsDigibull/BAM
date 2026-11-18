"""#756 (WG-8) SubSkillBuild with the choose-a-repository step: real child workflow on Temporal's
time-skipping server, the `choose_repo` stage overridden per test (the real one asks a model and
reads a database; see tests/unit/test_wg8_choose_repo.py). See test_sub_skill_build.py for the
harness.

- WG8-T01: a decided repository (clear winner) goes straight on to the build, once.
- WG8-T02: a tie pauses the build in WAIT_REPO with no deadline, and says so in `build_status`.
- WG8-T03: the FDE's answer resumes the build and reaches the stage; a repeated key signals once.
- WG8-T04: "no repository" resumes the build with the generated outcome.
- Reminder: the FDE is reminded every 3 days and the build keeps waiting.
- Replay: histories recorded before the step replay unchanged (it is behind `workflow.patched`),
  and the history of a build that asked and was answered replays on its own.
"""

import asyncio
import json
import os
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sbam_contracts.build import BuildStage, BuildStageInput
from sbam_contracts.repo_choice import (
    REPO_ANSWER_SIGNAL,
    ChooseRepoInput,
    ChooseRepoResult,
    ChooseRepoStatus,
    RepoAnswerAction,
    RepoAnswerSignal,
)
from sbam_contracts.repo_decision import RepoOutcome
from sbam_contracts.runs import build_workflow_id
from sbam_workers.pipeline.sub_skill_build import (
    MODEL_STAGES,
    REPO_CHOICE_PATCH,
    REPO_REMINDER_AFTER,
    SPECS,
    SubSkillBuild,
)
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer
from test_sub_skill_build import FDE, HISTORY, Build, run, save_history, traced
from test_sub_skill_build_review import patch_ids, scheduled

REPO_HISTORY = HISTORY.with_name("sub_skill_build_repo_choice.json")
URL = "https://github.com/acme/widgets"
DECIDED_GENERATED = ChooseRepoResult(status=ChooseRepoStatus.DECIDED, outcome=RepoOutcome.GENERATED)


def asks_then_decides(calls: list[ChooseRepoInput]) -> Any:
    """What the real stage does for a tie: ASKED without an answer, DECIDED with one."""

    def behave(inp: ChooseRepoInput) -> ChooseRepoResult:
        calls.append(inp)
        if inp.answer is None:
            return ChooseRepoResult(status=ChooseRepoStatus.ASKED)
        if inp.answer.action is RepoAnswerAction.NO_REPO:
            return DECIDED_GENERATED
        return ChooseRepoResult(
            status=ChooseRepoStatus.DECIDED, outcome=RepoOutcome.REPO, url=inp.answer.url
        )

    return behave


def answer(
    action: RepoAnswerAction, url: str | None = None, key: str = "ans-1"
) -> RepoAnswerSignal:
    return RepoAnswerSignal(action=action, url=url, idempotency_key=key, actor_id=FDE)


async def until_asking(build: Build) -> Any:
    return await build.until(lambda s: s.waiting_for_repo_choice)


def test_wg8_the_stage_is_a_model_stage_on_the_llm_queue() -> None:
    spec = SPECS[BuildStage.CHOOSE_REPO]
    assert BuildStage.CHOOSE_REPO in MODEL_STAGES  # a model that cannot answer parks the build
    assert spec.queue.value == "sbam-llm"
    assert {"RepoAnswerInvalid", "RepoQuestionMissing"} <= set(spec.non_retryable)
    assert REPO_REMINDER_AFTER == timedelta(days=3)


# --- WG8-T01 ------------------------------------------------------------------------------------


def test_wg8_t01_a_decided_repository_goes_straight_on_to_the_build_once() -> None:
    calls: list[ChooseRepoInput] = []
    manifests: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        return await build.finish(), await build.status(), build.state()

    def decided(inp: ChooseRepoInput) -> ChooseRepoResult:
        calls.append(inp)
        return ChooseRepoResult(status=ChooseRepoStatus.DECIDED, outcome=RepoOutcome.REPO, url=URL)

    result, status, state = run(
        scenario,
        choose_repo=traced("choose_repo", [], decided),
        build_manifest=traced("build_manifest", manifests),
    )
    assert result.certified and state == "CERTIFIED"
    assert len(calls) == 1 and calls[0].answer is None  # asked nothing, once per build
    assert status.waiting_for_repo_choice is False and status.repo_answer_keys == []


# --- WG8-T02 ------------------------------------------------------------------------------------


def test_wg8_t02_a_tie_pauses_the_build_with_no_deadline_and_says_so() -> None:
    calls: list[ChooseRepoInput] = []
    manifests: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        waiting = await until_asking(build)
        await asyncio.sleep(0.3)
        later = await build.status()
        await build.end()
        return waiting, later, build.state()

    waiting, later, state = run(
        scenario,
        choose_repo=traced("choose_repo", [], asks_then_decides(calls)),
        build_manifest=traced("build_manifest", manifests),
    )
    assert waiting.stage is BuildStage.WAIT_REPO and waiting.state.value == "BUILDING"
    assert waiting.waiting_for_repo_choice and not waiting.waiting_for_retry
    assert later.waiting_for_repo_choice  # still waiting: nothing times out
    assert manifests == []  # the build did not start before the answer
    assert len(calls) == 1 and state == "BUILDING"


# --- WG8-T03 ------------------------------------------------------------------------------------


def test_wg8_t03_the_answer_resumes_the_build_and_reaches_the_stage() -> None:
    calls: list[ChooseRepoInput] = []

    async def scenario(build: Build) -> Any:
        await until_asking(build)
        await build.handle.signal(REPO_ANSWER_SIGNAL, answer(RepoAnswerAction.CHOOSE, URL))
        return await build.finish(), await build.status(), build.state()

    result, status, state = run(
        scenario, choose_repo=traced("choose_repo", [], asks_then_decides(calls))
    )
    assert result.certified and state == "CERTIFIED"
    assert [c.answer is not None for c in calls] == [False, True]  # ask, then record the answer
    assert calls[1].answer is not None
    assert (calls[1].answer.url, calls[1].answer.actor_id) == (URL, FDE)
    assert status.repo_answer_keys == ["ans-1"] and not status.waiting_for_repo_choice


def test_wg8_t03_a_repeated_key_is_one_answer_and_a_late_answer_is_ignored() -> None:
    calls: list[ChooseRepoInput] = []

    async def scenario(build: Build) -> Any:
        await until_asking(build)
        signal = answer(RepoAnswerAction.CHOOSE, URL)
        await build.handle.signal(REPO_ANSWER_SIGNAL, signal)
        await build.handle.signal(REPO_ANSWER_SIGNAL, signal)  # the same request again
        await build.handle.signal(REPO_ANSWER_SIGNAL, answer(RepoAnswerAction.NO_REPO, key="ans-2"))
        return await build.finish()

    result = run(scenario, choose_repo=traced("choose_repo", [], asks_then_decides(calls)))
    assert result.certified
    assert len(calls) == 2 and calls[1].answer is not None and calls[1].answer.url == URL


def test_wg8_a_cancel_while_asking_ends_the_build_uncertified() -> None:
    async def scenario(build: Build) -> Any:
        await until_asking(build)
        return await build.end(), await build.status()

    calls: list[ChooseRepoInput] = []
    result, status = run(scenario, choose_repo=traced("choose_repo", [], asks_then_decides(calls)))
    assert not result.certified and not status.waiting_for_repo_choice


# --- WG8-T04 ------------------------------------------------------------------------------------


def test_wg8_t04_no_repo_resumes_the_build_with_generation() -> None:
    calls: list[ChooseRepoInput] = []

    async def scenario(build: Build) -> Any:
        await until_asking(build)
        await build.handle.signal(REPO_ANSWER_SIGNAL, answer(RepoAnswerAction.NO_REPO))
        return await build.finish(), build.state()

    result, state = run(scenario, choose_repo=traced("choose_repo", [], asks_then_decides(calls)))
    assert result.certified and state == "CERTIFIED"
    assert calls[1].answer is not None and calls[1].answer.action is RepoAnswerAction.NO_REPO


# --- a model that cannot answer, a retry, the reminder ------------------------------------------


def test_wg8_a_budget_refusal_parks_the_build_and_retry_asks_again() -> None:
    calls: list[ChooseRepoInput] = []

    def refuses_once(inp: ChooseRepoInput) -> ChooseRepoResult | None:
        calls.append(inp)
        if len(calls) == 1:
            raise ApplicationError("budget (test)", type="BudgetExceeded", non_retryable=True)
        return DECIDED_GENERATED

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        await build.retry()
        return waiting, await build.finish()

    waiting, result = run(scenario, choose_repo=traced("choose_repo", [], refuses_once))
    assert waiting.state.value == "NEEDS_FDE_REVIEW"
    assert result.certified and len(calls) == 2  # the new cycle asked again


def test_wg8_the_fde_is_reminded_every_three_days_and_the_build_keeps_waiting() -> None:
    calls: list[ChooseRepoInput] = []

    async def scenario(build: Build) -> Any:
        await until_asking(build)
        # Awaiting the result lets the test server skip time: each skip is one 3 day reminder.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(build.finish(), timeout=3)
        still = await build.status()
        reminders = [a for a in build.actions() if a == "sub_skill.repo_choice_reminder"]
        await build.handle.signal(REPO_ANSWER_SIGNAL, answer(RepoAnswerAction.NO_REPO))
        return still, reminders, await build.finish(), build.actions()

    still, reminders, result, actions = run(
        scenario, choose_repo=traced("choose_repo", [], asks_then_decides(calls))
    )
    assert still.waiting_for_repo_choice  # three days later it still waits
    assert len(reminders) >= 1
    assert actions[0] == "sub_skill.repo_question"  # the question itself is announced first
    assert result.certified


# --- replay --------------------------------------------------------------------------------------


def test_wg8_histories_recorded_before_the_step_replay_unchanged() -> None:
    """The step is behind `workflow.patched("wg8-choose-repo")`: a history with no marker and no
    `choose_repo` event (every one recorded before #756) replays on the new code."""
    raw = json.loads(HISTORY.read_text("utf-8"))
    assert REPO_CHOICE_PATCH not in patch_ids(raw) and scheduled(raw, "choose_repo") == 0
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


def test_wg8_replays_the_recorded_history_of_a_build_that_asked_and_was_answered() -> None:
    raw = json.loads(REPO_HISTORY.read_text("utf-8"))
    assert REPO_CHOICE_PATCH in patch_ids(raw) and scheduled(raw, "choose_repo") == 2
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_repo_choice_history() -> None:  # pragma: no cover - maintenance helper
    """#756: a tie asks, the FDE chooses, the build certifies."""
    calls: list[ChooseRepoInput] = []

    async def scenario(build: Build) -> dict[str, Any]:
        await until_asking(build)
        await build.handle.signal(REPO_ANSWER_SIGNAL, answer(RepoAnswerAction.CHOOSE, URL))
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    data = run(scenario, choose_repo=traced("choose_repo", [], asks_then_decides(calls)))
    save_history(REPO_HISTORY, data)
