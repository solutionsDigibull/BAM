"""#117 SubSkillBuild when a code gate reports a platform defect: a bundle that no longer matches
what `assemble` recorded (BUNDLE_TAMPERED) or a gate given the wrong inputs
(AST_GATE_INPUT_MISMATCH) is BUILD_ERROR with a notification, not a crashed workflow nobody hears
about. Real child workflow on Temporal's time-skipping server, stub stages overridden per test (see
test_sub_skill_build.py for the harness)."""

import asyncio
import json
import os
import uuid
from typing import Any

import pytest
from sbam_contracts.runs import build_workflow_id
from sbam_workers.build.gates.errors import (
    BUNDLE_TAMPERED,
    GATE_INPUT_MISMATCH,
    STATIC_GATE_INFRA,
    SUITE_INVALID,
)
from sbam_workers.pipeline.sub_skill_build import SubSkillBuild
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer
from test_sub_skill_build import HISTORY, Build, run, save_history, traced

PLATFORM_DEFECT_HISTORY = HISTORY.with_name("sub_skill_build_platform_defect.json")


def raises(kind: str) -> Any:
    def behave(inp: Any) -> None:
        raise ApplicationError(f"{kind} (secret detail)", type=kind, non_retryable=True)

    return behave


def notice(build: Build) -> dict[str, Any]:
    [row] = [r for r in build.audit() if r.action == "sub_skill.build_error"]
    assert row.after is not None
    after: dict[str, Any] = row.after
    return after


@pytest.mark.parametrize("kind", [BUNDLE_TAMPERED, GATE_INPUT_MISMATCH])
def test_e12_2_a_gate_platform_defect_is_a_build_error_with_a_notification(kind: str) -> None:
    corrections: list[Any] = []

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.actions(), build.state(), notice(build)

    result, actions, state, said = run(
        scenario,
        ast_gate=traced("ast_gate", [], raises(kind)),
        correct=traced("correct", corrections),
    )
    assert not result.certified and state == "BUILD_ERROR"
    assert actions == ["template_error", "sub_skill.build_error"]
    assert said["error"] == kind and said["stage"] == "code build"  # the type, never the message
    assert "secret detail" not in json.dumps(said)
    assert corrections == []  # a platform defect is not something the corrector can fix


def test_e12_4_t07_a_static_tool_with_no_verdict_is_retried_then_a_build_error() -> None:
    """clamd down or a tool that keeps timing out: two attempts, no correction is consumed, then
    BUILD_ERROR with a notification an operator can act on."""
    calls: list[Any] = []
    corrections: list[Any] = []

    def no_verdict(inp: Any) -> None:
        calls.append(inp.attempt)
        raise ApplicationError("cannot reach clamd (host)", type=STATIC_GATE_INFRA)

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.state(), notice(build)

    result, state, said = run(
        scenario,
        static_gate=traced("static_gate", [], no_verdict),
        correct=traced("correct", corrections),
    )
    assert not result.certified and state == "BUILD_ERROR"
    assert len(calls) == 2 and corrections == []  # retried by Temporal, never corrected
    assert said["error"] == STATIC_GATE_INFRA and "host" not in json.dumps(said)


def test_e12_2_a_template_error_names_itself_in_the_notification_too() -> None:
    async def scenario(build: Build) -> Any:
        return await build.finish(), notice(build)

    result, said = run(
        scenario,
        render_skeleton=traced("render_skeleton", [], raises("TemplateError")),
    )
    assert not result.certified
    assert said["error"] == "TemplateError" and said["stage"] == "code build"


def test_e12_2_a_wiring_error_in_another_stage_still_fails_the_workflow_loudly() -> None:
    """Only the gate's two defects are mapped; another stage's `InputMismatch` is not."""

    async def scenario(build: Build) -> Any:
        with pytest.raises(WorkflowFailureError):
            await build.finish()
        return build.actions(), build.state()

    actions, state = run(scenario, ast_gate=traced("ast_gate", [], raises("InputMismatch")))
    assert actions == [] and state == "BUILDING"


def test_e12_2_replays_the_recorded_platform_defect_history() -> None:
    """A build that stopped on a tampered bundle keeps replaying (the change is behind
    workflow.patched; older histories replay in test_replay_histories)."""
    workflow_id = build_workflow_id(uuid.UUID(int=0), 1)
    history = WorkflowHistory.from_json(
        workflow_id, json.loads(PLATFORM_DEFECT_HISTORY.read_text("utf-8"))
    )
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_platform_defect_history() -> None:  # pragma: no cover - maintenance helper
    """#117: the gate finds the stored bundle changed after assembly: BUILD_ERROR."""

    async def scenario(build: Build) -> dict[str, Any]:
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    data = run(scenario, ast_gate=traced("ast_gate", [], raises(BUNDLE_TAMPERED)))
    save_history(PLATFORM_DEFECT_HISTORY, data)


def test_e13_6_a_suite_that_cannot_be_built_is_a_build_error_not_a_correction() -> None:
    corrections: list[Any] = []

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.state(), notice(build)

    result, state, said = run(
        scenario,
        build_promptfoo_suite=traced("build_promptfoo_suite", [], raises(SUITE_INVALID)),
        correct=traced("correct", corrections),
    )
    assert not result.certified and state == "BUILD_ERROR"
    assert said["error"] == SUITE_INVALID and corrections == []
