"""#862 (WG-28) SubSkillBuild attaches the chosen repository's scan report and overlay: real child
workflow on Temporal's time-skipping server, `choose_repo` and `attach_repo` overridden per test
(the real ones read a database and clone a repository; see tests/unit/test_wg28_*.py). See
test_sub_skill_build.py for the harness.

- WG28-T01: a decided repository is attached once, with the chosen URL, before the build.
- WG28-T02: no repository (generated outcome) attaches nothing.
- WG28-T03: the FDE's answer is attached too, and a new cycle does not attach again.
- WG28-T04: a fetch that cannot answer does not stop the build.
- Replay: histories recorded before the step replay unchanged (it is behind `workflow.patched`),
  and the history of a build that attached replays on its own.
"""

import asyncio
import json
import os
import uuid
from typing import Any

import pytest
from sbam_api.builds.repo_attached import AttachStatus
from sbam_contracts.build import BuildStage, BuildStageInput
from sbam_contracts.repo_choice import (
    REPO_ANSWER_SIGNAL,
    ChooseRepoInput,
    ChooseRepoResult,
    ChooseRepoStatus,
    RepoAnswerAction,
)
from sbam_contracts.repo_decision import RepoOutcome
from sbam_contracts.runs import build_workflow_id
from sbam_workers.build.repo_attach import AttachRepoInput, AttachRepoResult
from sbam_workers.pipeline.sub_skill_build import REPO_ATTACH_PATCH, SubSkillBuild
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer
from test_project_pipeline import attach_repo_stand_in
from test_sub_skill_build import HISTORY, ORIGINALS, Build, run, save_history, traced
from test_sub_skill_build_repo_choice import (
    DECIDED_GENERATED,
    URL,
    answer,
    asks_then_decides,
    until_asking,
)
from test_sub_skill_build_review import patch_ids, scheduled

ORIGINALS["attach_repo"] = attach_repo_stand_in  # the harness's stand-in of the real stage
ATTACH_HISTORY = HISTORY.with_name("sub_skill_build_attach_repo.json")
REPORT = "a" * 64


def decided_repo(calls: list[ChooseRepoInput] | None = None) -> Any:
    def behave(inp: ChooseRepoInput) -> Any:
        if calls is not None:
            calls.append(inp)
        return ChooseRepoResult(status=ChooseRepoStatus.DECIDED, outcome=RepoOutcome.REPO, url=URL)

    return behave


def attached(calls: list[AttachRepoInput]) -> Any:
    def behave(inp: AttachRepoInput) -> AttachRepoResult:
        calls.append(inp)
        return AttachRepoResult(status=AttachStatus.ATTACHED, commit="b" * 40, report_sha256=REPORT)

    return behave


def test_wg28_t01_a_decided_repository_is_attached_once_before_the_build() -> None:
    chosen: list[ChooseRepoInput] = []
    calls: list[AttachRepoInput] = []
    manifests: list[BuildStageInput] = []
    order: list[str] = []

    def mark(name: str, inner: Any = None) -> Any:
        def behave(inp: Any) -> Any:
            order.append(name)
            return None if inner is None else inner(inp)

        return behave

    async def scenario(build: Build) -> Any:
        return await build.finish(), await build.status(), build.state()

    result, status, state = run(
        scenario,
        choose_repo=traced("choose_repo", [], mark("choose", decided_repo(chosen))),
        attach_repo=traced("attach_repo", [], mark("attach", attached(calls))),
        build_manifest=traced("build_manifest", manifests, mark("manifest")),
    )
    assert result.certified and state == "CERTIFIED"
    assert order == ["choose", "attach", "manifest"]  # the report is attached before the manifest
    assert len(calls) == 1 and calls[0].url == URL
    assert (calls[0].build_no, calls[0].cycle, calls[0].attempt) == (1, 0, 0)
    assert status.stage is BuildStage.DONE


def test_wg28_t02_no_repository_attaches_nothing() -> None:
    calls: list[AttachRepoInput] = []

    async def scenario(build: Build) -> Any:
        return await build.finish()

    result = run(
        scenario,
        choose_repo=traced("choose_repo", [], lambda inp: DECIDED_GENERATED),
        attach_repo=traced("attach_repo", [], attached(calls)),
    )
    assert result.certified
    assert calls == []  # a build without a repository is not an error and attaches nothing


def test_wg28_t03_the_fdes_answer_is_attached_and_a_new_cycle_does_not_attach_again() -> None:
    asked: list[ChooseRepoInput] = []
    calls: list[AttachRepoInput] = []

    failures: list[int] = []

    def fails_once(inp: Any) -> None:
        if not failures:
            failures.append(inp.attempt)
            raise ApplicationError(
                "gate failed", [str(uuid.uuid4())], type="GateFailed", non_retryable=True
            )

    async def scenario(build: Build) -> Any:
        await until_asking(build)
        await build.handle.signal(REPO_ANSWER_SIGNAL, answer(RepoAnswerAction.CHOOSE, URL))
        return await build.finish()

    result = run(
        scenario,
        choose_repo=traced("choose_repo", [], asks_then_decides(asked)),
        attach_repo=traced("attach_repo", [], attached(calls)),
        ast_gate=traced("ast_gate", [], fails_once),
    )
    assert result.certified
    assert len(asked) == 2  # asked, then the answer
    assert [c.url for c in calls] == [
        URL
    ]  # once, after the answer; a correction is not a new build
    assert failures == [0]


def test_wg28_t04_a_fetch_that_cannot_answer_does_not_stop_the_build() -> None:
    manifests: list[BuildStageInput] = []

    def unreachable(inp: AttachRepoInput) -> None:
        raise ApplicationError("git is unreachable (test)", type="CloneUnavailable")

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.state()

    result, state = run(
        scenario,
        choose_repo=traced("choose_repo", [], decided_repo()),
        attach_repo=traced("attach_repo", [], unreachable),
        build_manifest=traced("build_manifest", manifests),
    )
    assert result.certified and state == "CERTIFIED"
    assert len(manifests) == 1  # the build went on and generated its own code


# --- replay --------------------------------------------------------------------------------------


def test_wg28_histories_recorded_before_the_step_replay_unchanged() -> None:
    """The step is behind `workflow.patched("wg28-attach-repo-report")`: a history with no marker
    and no `attach_repo` event (every one recorded before #862) replays on the new code."""
    raw = json.loads(HISTORY.read_text("utf-8"))
    assert REPO_ATTACH_PATCH not in patch_ids(raw) and scheduled(raw, "attach_repo") == 0
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


def test_wg28_the_repo_choice_history_of_the_earlier_step_replays_unchanged() -> None:
    """The #756 history chose a repository with the new step absent: no marker, no attach."""
    raw = json.loads(HISTORY.with_name("sub_skill_build_repo_choice.json").read_text("utf-8"))
    assert REPO_ATTACH_PATCH not in patch_ids(raw) and scheduled(raw, "attach_repo") == 0
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


def test_wg28_replays_the_recorded_history_of_a_build_that_attached() -> None:
    raw = json.loads(ATTACH_HISTORY.read_text("utf-8"))
    assert REPO_ATTACH_PATCH in patch_ids(raw) and scheduled(raw, "attach_repo") == 1
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_attach_repo_history() -> None:  # pragma: no cover - maintenance helper
    """#862: a repository is decided, attached, and the build certifies."""

    async def scenario(build: Build) -> dict[str, Any]:
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    data = run(
        scenario,
        choose_repo=traced("choose_repo", [], decided_repo()),
        attach_repo=traced("attach_repo", [], attached([])),
    )
    save_history(ATTACH_HISTORY, data)
