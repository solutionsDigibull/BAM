"""#761 (WG-13) SubSkillBuild with the compliance gates (and, before #922, the import gate): after
the AST and static gates the licence, dependency vulnerability and MCP compliance rows are recorded,
all before the review and the tests. Real child workflow on Temporal's time-skipping server, stub
stages overridden per test (see test_sub_skill_build.py).

- A failed compliance row goes to the corrector like any gate.
- #922 (ADR 0040): a new build does not run the import gate at all (`wg35-no-import-gate`).
- Replay: histories recorded before these stages keep replaying (they are behind
  `workflow.patched`); a build that ran the import gate replays it from its own history.
"""

import asyncio
import json
import os
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sbam_contracts.build import BuildStage, BuildStageInput
from sbam_contracts.runs import TaskQueue, build_workflow_id
from sbam_workers.build.gates.errors import IMPORT_GATE_INFRA
from sbam_workers.pipeline.sub_skill_build import (
    PLATFORM_DEFECTS,
    SPECS,
    SubSkillBuild,
)
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer
from test_sub_skill_build import HISTORY, Build, gate_failed, run, save_history, traced
from test_sub_skill_build_review import patch_ids, scheduled

IMPORT_HISTORY = HISTORY.with_name("sub_skill_build_import_gates.json")
PATCH = "wg13-import-and-compliance-gates"
NO_IMPORT_PATCH = "wg35-no-import-gate"
AFTER_STATIC = [
    "compliance_gates",
    "review_code",
    "generate_tests",
    "build_promptfoo_suite",
    "run_sandbox_tests",
    "certify_sub_skill",
]


def fails_on(attempts: set[int]) -> Callable[[BuildStageInput], None]:
    def behave(inp: BuildStageInput) -> None:
        if inp.cycle == 0 and inp.attempt in attempts:
            gate_failed()

    return behave


# --- the stages ---------------------------------------------------------------------------------


def test_wg13_the_specs_put_both_stages_on_the_build_queue_with_gate_failed_non_retryable() -> None:
    for stage in (BuildStage.IMPORT_GATE, BuildStage.COMPLIANCE_GATES):
        spec = SPECS[stage]
        assert spec.queue is TaskQueue.BUILD and "GateFailed" in spec.non_retryable
    assert IMPORT_GATE_INFRA not in SPECS[BuildStage.IMPORT_GATE].non_retryable  # retried
    assert IMPORT_GATE_INFRA in PLATFORM_DEFECTS  # and then a platform defect


def test_wg13_the_compliance_stage_runs_once_after_the_static_gate_and_before_the_review() -> None:
    """#922: the import gate is no longer one of the stages (replaces `both_stages_run_once...`)."""
    order: list[str] = []
    gate_calls: list[BuildStageInput] = []

    def recorder(name: str) -> Callable[[Any], None]:
        return lambda inp: order.append(name)

    names = ["ast_gate", "static_gate", *AFTER_STATIC]
    overrides = {name: traced(name, [], recorder(name)) for name in names}

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.actions(), build.state()

    result, actions, state = run(
        scenario, import_gate=traced("import_gate", gate_calls), **overrides
    )
    assert order == names
    assert gate_calls == []  # IMP-T08: nothing produces an IMPORT_RESOLUTION row any more
    assert result.certified and state == "CERTIFIED"
    assert actions == ["tests_started", "tests_passed", "sub_skill.certified"]


def test_imp_t08_a_new_build_records_the_patch_that_skips_the_import_gate() -> None:
    async def scenario(build: Build) -> dict[str, Any]:
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    raw = run(scenario)
    assert PATCH in patch_ids(raw) and NO_IMPORT_PATCH in patch_ids(raw)
    assert scheduled(raw, "import_gate") == 0 and scheduled(raw, "compliance_gates") == 1


def test_wg13_a_failed_row_goes_to_the_corrector_and_the_next_pass_certifies() -> None:
    stage = "compliance_gates"
    corrections: list[Any] = []
    calls: list[BuildStageInput] = []
    reviews: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        return await build.finish(), build.actions()

    result, actions = run(
        scenario,
        correct=traced("correct", corrections),
        review_code=traced("review_code", reviews),
        **{stage: traced(stage, calls, fails_on({0}))},
    )
    assert result.certified
    assert [(c.attempt, c.failed_gate) for c in corrections] == [(0, stage)]
    assert [c.attempt for c in calls] == [0, 1]
    assert [r.attempt for r in reviews] == [1]  # the review never saw the failed pass
    assert actions == [
        "gate_failed",
        "correction_applied",
        "tests_started",
        "tests_passed",
        "sub_skill.certified",
    ]


def test_wg13_no_tests_run_for_code_that_keeps_failing_a_compliance_gate() -> None:
    tests: list[BuildStageInput] = []

    async def scenario(build: Build) -> Any:
        waiting = await build.until_waiting()  # corrections spent: parked for the FDE
        state = build.state()
        await build.end()
        return waiting, state

    waiting, state = run(
        scenario,
        compliance_gates=traced("compliance_gates", [], fails_on({0, 1, 2})),
        run_sandbox_tests=traced("run_sandbox_tests", tests),
    )
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and state == "NEEDS_FDE_REVIEW"
    assert tests == []  # nothing past the compliance gates ever ran


# --- replay -------------------------------------------------------------------------------------


def test_wg13_histories_recorded_before_the_import_gates_replay_unchanged() -> None:
    raw = json.loads(HISTORY.read_text("utf-8"))
    assert PATCH not in patch_ids(raw) and scheduled(raw, "import_gate") == 0
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


def test_wg13_replays_the_recorded_history_of_a_build_that_ran_the_new_stages() -> None:
    """The import gate failed once, the corrector fixed it, the second pass certified."""
    raw = json.loads(IMPORT_HISTORY.read_text("utf-8"))
    assert PATCH in patch_ids(raw)
    assert scheduled(raw, "import_gate") == 2 and scheduled(raw, "compliance_gates") == 1
    history = WorkflowHistory.from_json(build_workflow_id(uuid.UUID(int=0), 1), raw)
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_import_gates_history() -> None:  # pragma: no cover - maintenance helper
    """#761: the import gate fails the first pass, the corrector fixes it, the second certifies."""

    async def scenario(build: Build) -> dict[str, Any]:
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    data = run(scenario, import_gate=traced("import_gate", [], fails_on({0})))
    save_history(IMPORT_HISTORY, data)
