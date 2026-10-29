"""#446 SubSkillBuild when a correction changes what the Sub-Skill means (A-20): a BEHAVIOUR ISATVON
patch is held for the FDE instead of carrying on to certification; a NEUTRAL one continues as
before. Real child workflow on Temporal's time-skipping server, stub stages overridden per test
(see test_sub_skill_build.py for the harness)."""

import asyncio
import json
import os
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sbam_contracts.build import (
    BuildStageInput,
    CorrectionInput,
    CorrectionResult,
    IsatvonPatchClass,
    LibraryCheckResult,
    LibraryStatus,
)
from sbam_contracts.runs import build_workflow_id
from sbam_workers.pipeline.sub_skill_build import SubSkillBuild, _summary
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
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

HELD_HISTORY = HISTORY.with_name("sub_skill_build_isatvon_review.json")
BOUND = "/isatvon_definition/v_variables/max_iterations"
LIBRARY = "/isatvon_definition/t_tech_stack/libraries/0/version_constraint"


def corrected(
    kind: IsatvonPatchClass, *paths: str
) -> Callable[[CorrectionInput], CorrectionResult]:
    """The corrector's answer: it made attempt N+1 and did `kind` to the ISATVON."""
    return lambda inp: CorrectionResult(
        id=uuid.uuid4(), isatvon_patch=kind, changed_paths=list(paths)
    )


def fails_while(flag: dict[str, bool]) -> Callable[[BuildStageInput], None]:
    def behave(inp: BuildStageInput) -> None:
        if flag["on"]:
            gate_failed()

    return behave


def notice(build: Build, action: str) -> dict[str, Any]:
    [row] = [r for r in build.audit() if r.action == action]
    assert row.after is not None
    after: dict[str, Any] = row.after
    return after


# --- A-20-T01: a behaviour-changing patch is held -------------------------------------------------


def test_a20_t01_behaviour_patch_requires_review() -> None:
    flag = {"on": True}
    gates: list[BuildStageInput] = []
    corrections: list[CorrectionInput] = []

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        before, said = build.actions(), notice(build, "sub_skill.isatvon_review")
        flag["on"] = False
        await build.retry()
        return waiting, before, said, await build.finish(), build.state()

    waiting, before, said, result, state = run(
        scenario,
        ast_gate=traced("ast_gate", gates, fails_while(flag)),
        correct=traced("correct", corrections, corrected(IsatvonPatchClass.BEHAVIOUR, BOUND)),
    )
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.waiting_for_retry
    assert waiting.corrections_used == 1  # held, not carried on to a second pass
    assert [g.attempt for g in gates if g.cycle == 0] == [0]  # no pass was built on the patch
    assert before == ["gate_failed", "isatvon_review_required", "sub_skill.isatvon_review"]
    assert said["patch"] == "BEHAVIOUR" and said["changed_paths"] == BOUND
    assert said["next_action"] == "retry" and said["finding_ids"] == str(FINDING)
    assert len(corrections) == 1  # the retry runs from the approved ISATVON and passes
    assert result.certified and state == "CERTIFIED"


def test_a20_t01_a_patch_held_by_the_second_correction_lists_both_attempts_findings() -> None:
    ids = [uuid.uuid4(), uuid.uuid4()]

    def gate(inp: BuildStageInput) -> None:
        from temporalio.exceptions import ApplicationError

        raise ApplicationError(
            "gate failed", [str(ids[inp.attempt])], type="GateFailed", non_retryable=True
        )

    answers = iter([IsatvonPatchClass.NEUTRAL, IsatvonPatchClass.BEHAVIOUR])

    def correction(inp: CorrectionInput) -> CorrectionResult:
        return corrected(next(answers), BOUND)(inp)

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        actions, said = build.actions(), notice(build, "sub_skill.isatvon_review")
        await build.end()
        return waiting, actions, said

    waiting, actions, said = run(
        scenario,
        ast_gate=traced("ast_gate", [], gate),
        correct=traced("correct", [], correction),
    )
    assert actions == [
        "gate_failed",
        "correction_applied",  # the neutral one carried on
        "gate_failed",
        "isatvon_review_required",
        "sub_skill.isatvon_review",
    ]
    assert waiting.corrections_used == 2 and waiting.finding_ids == ids
    assert said["finding_ids"] == ",".join(str(i) for i in ids)


# --- A-20-T02: a neutral patch carries on ---------------------------------------------------------


@pytest.mark.parametrize("kind", [IsatvonPatchClass.NEUTRAL, IsatvonPatchClass.NONE])
def test_a20_t02_neutral_patch_continues(kind: IsatvonPatchClass) -> None:
    gates: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.actions()

    paths = (LIBRARY,) if kind is IsatvonPatchClass.NEUTRAL else ()
    result, actions = run(
        scenario,
        ast_gate=traced("ast_gate", gates, lambda inp: gate_failed() if inp.attempt == 0 else None),
        correct=traced("correct", [], corrected(kind, *paths)),
    )
    assert result.certified
    assert [g.attempt for g in gates] == [0, 1]  # the next pass ran on the corrected build
    assert actions == [
        "gate_failed",
        "correction_applied",
        "tests_started",
        "tests_passed",
        "sub_skill.certified",
    ]


# --- a library fix is corrected in DEPENDENCY_FAILED: a failure state, it stays there -------------


def test_a20_a_behaviour_patch_in_dependency_failed_waits_there() -> None:
    flag = {"on": True}

    def missing(inp: BuildStageInput) -> LibraryCheckResult | None:
        if flag["on"]:
            return LibraryCheckResult(status=LibraryStatus.MISSING, finding_ids=[FINDING])
        return None

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()
        said = notice(build, "sub_skill.dependency_failed")
        actions = build.actions()
        flag["on"] = False
        await build.retry()
        return waiting, said, actions, await build.finish()

    waiting, said, actions, result = run(
        scenario,
        check_libraries=traced("check_libraries", [], missing),
        correct=traced("correct", [], corrected(IsatvonPatchClass.BEHAVIOUR, BOUND)),
    )
    assert waiting.state.value == "DEPENDENCY_FAILED"  # a failure state only leaves to BUILDING
    assert actions == ["dependency_missing", "sub_skill.dependency_failed"]
    assert said["patch"] == "BEHAVIOUR" and said["changed_paths"] == BOUND
    assert result.certified


def test_a20_the_hold_ends_with_the_cycle() -> None:
    """After the FDE's retry the next cycle is judged on its own: an ordinary exhaustion."""
    corrections = iter(
        [IsatvonPatchClass.BEHAVIOUR, IsatvonPatchClass.NONE, IsatvonPatchClass.NONE]
    )

    def correction(inp: CorrectionInput) -> CorrectionResult:
        kind = next(corrections)
        return corrected(kind, *((BOUND,) if kind is IsatvonPatchClass.BEHAVIOUR else ()))(inp)

    async def scenario(build: Build) -> Any:
        await build.until_waiting()
        await build.retry()
        waiting = await build.until_waiting(cycle=1)
        actions = build.actions()
        await build.end()
        return waiting, actions

    waiting, actions = run(
        scenario,
        ast_gate=traced("ast_gate", [], gate_failed),
        correct=traced("correct", [], correction),
    )
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.corrections_used == 2
    assert actions.count("isatvon_review_required") == 1  # cycle 0 only
    assert actions[-2:] == ["attempts_exhausted", "sub_skill.needs_fde_review"]


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "/p0"),
        (10, " ".join(f"/p{i}" for i in range(10))),
        (13, " ".join(f"/p{i}" for i in range(10)) + " (+3 more)"),
    ],
)
def test_a20_the_notification_names_the_first_paths_and_how_many_more(
    count: int, expected: str
) -> None:
    assert _summary([f"/p{i}" for i in range(count)]) == expected


# --- replay ---------------------------------------------------------------------------------------


def test_a20_replays_the_recorded_held_patch_history() -> None:
    """A build that held a behaviour patch and certified after the FDE's retry keeps replaying (the
    change is behind workflow.patched; older histories replay in test_replay_histories)."""
    workflow_id = build_workflow_id(uuid.UUID(int=0), 1)
    history = WorkflowHistory.from_json(workflow_id, json.loads(HELD_HISTORY.read_text("utf-8")))
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_isatvon_review_history() -> None:  # pragma: no cover - maintenance helper
    """#446: a correction changes the ISATVON's meaning (NEEDS_FDE_REVIEW), the FDE retries, the
    build certifies."""
    flag = {"on": True}

    async def scenario(build: Build) -> dict[str, Any]:
        await build.until_waiting()
        flag["on"] = False
        await build.retry()
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    data = run(
        scenario,
        ast_gate=traced("ast_gate", [], fails_while(flag)),
        correct=traced("correct", [], corrected(IsatvonPatchClass.BEHAVIOUR, BOUND)),
    )
    save_history(HELD_HISTORY, data)
