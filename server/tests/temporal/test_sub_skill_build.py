"""E10.2 SubSkillBuild against Temporal's time-skipping test server (no Docker).

Real child workflow + stub stage activities (the real stages belong to other issues), overridden
per test to fail, block or record. State transitions run through the real #41 service into the
SQLite stand-in `sub_skills` table, so every transition and notification is checked.
"""

import asyncio
import json
import os
import typing
import uuid
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from pipeline_db import pipeline_sessions, state_of
from sbam_api.audit.models import AuditLog
from sbam_api.builds.service import NotInReview, retry_build
from sbam_api.core.conventions import ApiError
from sbam_api.state_machine.registry import entity_model, register_entity, unregister_entity
from sbam_contracts.build import (
    BuildStageInput,
    CorrectionInput,
    LibraryCheckResult,
    LibraryStatus,
    SubSkillBuildStatus,
    SubSkillRetry,
)
from sbam_contracts.pipeline import Cancel, IdResult, SubSkillBuildInput, SubSkillBuildResult
from sbam_contracts.runs import build_workflow_id
from sbam_contracts.state_machines import MachineName
from sbam_workers import mirror
from sbam_workers.build import stubs as build_stubs
from sbam_workers.certify import stubs as certify_stubs
from sbam_workers.pipeline.sub_skill_build import SubSkillBuild
from sbam_workers.test import stubs as test_stubs
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from state_db import SQLITE_MODELS, add
from temporalio import activity
from temporalio.client import (
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowHistory,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.worker import Replayer
from test_project_pipeline import environment, libraries_stand_in, manifest_stand_in

TENANT = uuid.uuid4()
FDE = str(uuid.uuid4())
FINDING = uuid.uuid4()
SUB_SKILL = SQLITE_MODELS[MachineName.SUB_SKILL]
HISTORY = Path(__file__).parent / "histories" / "sub_skill_build.json"
CANCELLED_HISTORY = HISTORY.with_name("sub_skill_build_cancelled.json")
TESTING_BLOCKED_HISTORY = HISTORY.with_name("sub_skill_build_testing_blocked.json")
STAGES = [  # TD §13.2, in order
    "build_manifest",
    "check_libraries",
    "render_skeleton",
    "generate_bodies",
    "assemble",
    "ast_gate",
    "static_gate",
    "generate_tests",
    "build_promptfoo_suite",
    "run_sandbox_tests",
    "certify_sub_skill",
]
ORIGINALS: dict[str, Callable[..., Any]] = {
    "build_manifest": manifest_stand_in,
    "check_libraries": libraries_stand_in,
} | {
    name: getattr(module, name)
    for module, names in (
        (build_stubs, [*STAGES[2:7], "correct"]),
        (test_stubs, ["generate_tests", "build_promptfoo_suite", "run_sandbox_tests"]),
        (certify_stubs, ["certify_sub_skill"]),
    )
    for name in names
}


# --- harness -------------------------------------------------------------------------------------


@contextmanager
def sub_skill_table() -> Iterator[None]:
    """The stand-in Sub-Skill table; afterwards the real one (E8.3) is registered again."""
    real = entity_model(MachineName.SUB_SKILL)
    register_entity(MachineName.SUB_SKILL, SUB_SKILL)
    try:
        yield
    finally:
        unregister_entity(MachineName.SUB_SKILL)
        if real is not None:
            register_entity(MachineName.SUB_SKILL, real)


def run(scenario: Callable[["Build"], Coroutine[Any, Any, Any]], **overrides: Any) -> Any:
    sessions = pipeline_sessions()
    mirror.configure(sessions)

    async def main() -> Any:
        async with environment(overrides) as env:
            project = uuid.uuid4()
            sub_skill = add(sessions, SUB_SKILL, "BUILDING", TENANT, project)
            handle = await env.client.start_workflow(
                SubSkillBuild.run,
                SubSkillBuildInput(
                    tenant_id=TENANT,
                    project_id=project,
                    run_id=uuid.uuid4(),
                    correlation_id="cid-e10-2",
                    workflow_id=uuid.uuid4(),
                    sub_skill_id=sub_skill,
                    build_no=1,
                ),
                id=build_workflow_id(sub_skill, 1),
                task_queue="sbam-build",
            )
            return await scenario(Build(handle, sessions, sub_skill))

    with sub_skill_table():
        return asyncio.run(main())


class Build:
    def __init__(
        self,
        handle: WorkflowHandle[Any, Any],
        sessions: sessionmaker[Session],
        sub_skill: uuid.UUID,
    ) -> None:
        self.handle = handle
        self.sessions = sessions
        self.sub_skill = sub_skill

    async def status(self) -> SubSkillBuildStatus:
        status: SubSkillBuildStatus = await self.handle.query(
            "build_status", result_type=SubSkillBuildStatus
        )
        return status

    async def until(self, ready: Callable[[SubSkillBuildStatus], bool]) -> SubSkillBuildStatus:
        last = None
        for _ in range(600):
            last = await self.status()
            if ready(last):
                return last
            await asyncio.sleep(0.05)
        raise AssertionError(f"build never got there; last {last}")

    async def until_waiting(self, cycle: int = 0) -> SubSkillBuildStatus:
        return await self.until(lambda s: s.waiting_for_retry and s.cycle == cycle)

    async def retry(self, key: str = "retry-1") -> None:
        await self.handle.signal(
            "retry", SubSkillRetry(reason="fixed the ISATVON", idempotency_key=key, actor_id=FDE)
        )

    async def finish(self) -> SubSkillBuildResult:
        result: SubSkillBuildResult = await self.handle.result()
        return result

    async def end(self) -> SubSkillBuildResult:
        """Stop a build that waits for the FDE."""
        await self.handle.signal("cancel", Cancel(reason="test over", actor_id=FDE))
        return await self.finish()

    def actions(self) -> list[str]:
        return [row.action for row in self.audit()]

    def audit(self) -> list[AuditLog]:
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "sub_skill",
                        AuditLog.entity_id == str(self.sub_skill),
                    )
                    .order_by(AuditLog.id)
                )
            )

    def state(self) -> str | None:
        return state_of(self.sessions, SUB_SKILL, self.sub_skill)


def traced(name: str, order: list[Any], behave: Callable[[Any], Any] | None = None) -> Any:
    """The stub stage `name`, recording each call; `behave(inp)` may raise or return instead."""
    original = ORIGINALS[name]
    hints = typing.get_type_hints(original)

    async def stage(inp: Any) -> Any:
        order.append(inp)
        if behave is not None:
            result = behave(inp)
            if result is not None:
                return result
        return await original(inp)

    stage.__annotations__ = {"inp": hints["inp"], "return": hints["return"]}
    return activity.defn(name=name)(stage)


def gate_failed(*_: Any) -> None:
    raise ApplicationError("gate failed", [str(FINDING)], type="GateFailed", non_retryable=True)


def only_when(condition: Callable[[Any], bool]) -> Callable[[Any], None]:
    def behave(inp: Any) -> None:
        if condition(inp):
            gate_failed()

    return behave


# --- T01: happy path -----------------------------------------------------------------------------


def test_e10_2_t01_happy_path_certifies_at_attempt_zero() -> None:
    order: list[tuple[str, int]] = []
    overrides: dict[str, Any] = {}

    def recorder(name: str) -> Callable[[Any], None]:
        return lambda inp: order.append((name, inp.attempt))

    for name in [*STAGES, "correct"]:
        overrides[name] = traced(name, [], recorder(name))

    async def scenario(build: Build) -> tuple[SubSkillBuildResult, list[str], str | None]:
        result = await build.finish()
        return result, build.actions(), build.state()

    result, actions, state = run(scenario, **overrides)
    assert result.certified
    assert order == [(name, 0) for name in STAGES]  # attempt 0 only, no correction
    assert state == "CERTIFIED"
    # one audit row (and outbox notification) per state change, then the certified notification
    assert actions == ["tests_started", "tests_passed", "sub_skill.certified"]


# --- T02: stops after two corrections ------------------------------------------------------------


def test_e10_2_t02_stops_after_two_corrections_then_waits_for_retry() -> None:
    corrections: list[CorrectionInput] = []
    gate_calls: list[BuildStageInput] = []

    async def scenario(build: Build) -> tuple[SubSkillBuildStatus, SubSkillBuildResult, Any]:
        waiting = await build.until_waiting()
        assert (await build.handle.describe()).status is WorkflowExecutionStatus.RUNNING
        result = await build.end()
        notice = [r for r in build.audit() if r.action == "sub_skill.needs_fde_review"]
        assert len(notice) == 1
        return waiting, result, notice[0].after

    waiting, result, after = run(
        scenario,
        ast_gate=traced("ast_gate", gate_calls, gate_failed),
        correct=traced("correct", corrections),
    )
    assert len(corrections) == 2  # exactly two corrections, never a third
    assert [c.attempt for c in corrections] == [0, 1]
    assert [c.attempt for c in gate_calls] == [0, 1, 2]
    assert waiting.state.value == "NEEDS_FDE_REVIEW" and waiting.corrections_used == 2
    assert waiting.finding_ids == [FINDING]
    assert not result.certified
    assert after["finding_ids"] == str(FINDING) and after["next_action"] == "retry"


def test_e10_2_t02_audit_trail_of_an_exhausted_build() -> None:
    async def scenario(build: Build) -> list[str]:
        await build.until_waiting()
        await build.end()
        return build.actions()

    actions = run(scenario, ast_gate=traced("ast_gate", [], gate_failed))
    assert actions == [
        "gate_failed",
        "correction_applied",
        "gate_failed",
        "correction_applied",
        "gate_failed",
        "attempts_exhausted",
        "sub_skill.needs_fde_review",
    ]


# --- T03: fixed on the first correction ----------------------------------------------------------


def test_e10_2_t03_fix_on_first_correction_certifies() -> None:
    corrections: list[CorrectionInput] = []
    static_calls: list[BuildStageInput] = []
    bodies: list[BuildStageInput] = []

    async def scenario(build: Build) -> tuple[SubSkillBuildResult, list[str]]:
        return await build.finish(), build.actions()

    result, actions = run(
        scenario,
        ast_gate=traced("ast_gate", [], only_when(lambda inp: inp.attempt == 0)),
        static_gate=traced("static_gate", static_calls),
        generate_bodies=traced("generate_bodies", bodies),
        correct=traced("correct", corrections),
    )
    assert result.certified
    assert len(corrections) == 1 and corrections[0].failed_gate == "ast_gate"
    assert corrections[0].finding_ids == [FINDING]
    assert [c.attempt for c in static_calls] == [1]  # reached STATIC only after the correction
    assert [c.attempt for c in bodies] == [0]  # the corrector supplies the later bodies
    assert actions == [
        "gate_failed",
        "correction_applied",
        "tests_started",
        "tests_passed",
        "sub_skill.certified",
    ]


def test_e10_2_gate_passing_after_the_second_correction_still_certifies() -> None:
    corrections: list[CorrectionInput] = []

    async def scenario(build: Build) -> SubSkillBuildStatus:
        assert (await build.finish()).certified
        return await build.status()

    status = run(
        scenario,
        ast_gate=traced("ast_gate", [], only_when(lambda inp: inp.attempt < 2)),
        correct=traced("correct", corrections),
    )
    assert len(corrections) == 2
    assert status.state.value == "CERTIFIED" and status.corrections_used == 2


def test_e10_2_correction_that_changes_nothing_still_counts_as_an_attempt() -> None:
    same = uuid.uuid4()

    @activity.defn(name="correct")
    async def identical(inp: CorrectionInput) -> IdResult:
        return IdResult(id=same)  # the same bodies every time

    async def scenario(build: Build) -> SubSkillBuildStatus:
        status = await build.until_waiting()
        await build.end()
        return status

    status = run(scenario, ast_gate=traced("ast_gate", [], gate_failed), correct=identical)
    assert status.corrections_used == 2 and status.state.value == "NEEDS_FDE_REVIEW"


def test_e10_2_corrector_that_gives_up_goes_to_fde_review() -> None:
    @activity.defn(name="correct")
    async def gives_up(inp: CorrectionInput) -> IdResult:
        raise ApplicationError(
            "schema invalid", type="SchemaInvalidAfterRetries", non_retryable=True
        )

    async def scenario(build: Build) -> SubSkillBuildStatus:
        status = await build.until_waiting()
        await build.end()
        return status

    status = run(scenario, ast_gate=traced("ast_gate", [], gate_failed), correct=gives_up)
    assert status.state.value == "NEEDS_FDE_REVIEW"


# --- T04: retry resets the counter ---------------------------------------------------------------


def test_e10_2_t04_retry_resets_the_correction_counter() -> None:
    corrections: list[CorrectionInput] = []
    manifests: list[BuildStageInput] = []
    bodies: list[BuildStageInput] = []

    async def scenario(
        build: Build,
    ) -> tuple[SubSkillBuildStatus, SubSkillBuildStatus, SubSkillBuildStatus]:
        first = await build.until_waiting(cycle=0)
        await build.retry("retry-1")
        second = await build.until_waiting(cycle=1)
        await build.retry("retry-1")  # the same Idempotency-Key again: not a second retry
        await asyncio.sleep(0.5)
        still = await build.status()
        await build.end()
        return first, second, still

    first, second, still = run(
        scenario,
        ast_gate=traced("ast_gate", [], gate_failed),
        correct=traced("correct", corrections),
        build_manifest=traced("build_manifest", manifests),
        generate_bodies=traced("generate_bodies", bodies),
    )
    assert first.corrections_used == 2 and second.corrections_used == 2
    assert len(corrections) == 4  # two more corrections after the retry, not zero
    assert second.state.value == "NEEDS_FDE_REVIEW" and second.cycle == 1
    assert (still.cycle, still.waiting_for_retry) == (1, True)
    # the retry re-runs everything from the manifest, bodies included; evidence keys differ
    assert [(m.cycle, m.attempt) for m in manifests] == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
    ]
    assert [(b.cycle, b.attempt) for b in bodies] == [(0, 0), (1, 0)]


def test_e10_2_retry_is_applied_in_the_fdes_name_and_fixes_the_build() -> None:
    broken = {"on": True}

    def behave(inp: Any) -> None:
        if broken["on"]:
            gate_failed()

    async def scenario(build: Build) -> tuple[SubSkillBuildResult, list[AuditLog]]:
        await build.until_waiting()
        broken["on"] = False  # the FDE fixed the ISATVON
        await build.retry()
        return await build.finish(), build.audit()

    result, audit = run(scenario, ast_gate=traced("ast_gate", [], behave))
    assert result.certified
    [retry] = [r for r in audit if r.action == "retry"]
    assert retry.actor_id == FDE
    assert [r.action for r in audit][-3:] == [
        "tests_started",
        "tests_passed",
        "sub_skill.certified",
    ]


def test_e10_2_retry_signal_is_ignored_unless_the_build_waits() -> None:
    async def scenario(build: Build) -> SubSkillBuildResult:
        await build.retry("early")  # arrives while it is still BUILDING / or already done
        return await build.finish()

    gate_calls: list[BuildStageInput] = []
    assert run(scenario, ast_gate=traced("ast_gate", gate_calls)).certified
    assert len(gate_calls) == 1


# --- T06: sandbox infrastructure failure ---------------------------------------------------------


def test_e10_2_t06_sandbox_down_blocks_testing_without_using_a_correction() -> None:
    down = {"on": True}
    sandbox: list[BuildStageInput] = []
    manifests: list[BuildStageInput] = []
    corrections: list[CorrectionInput] = []

    def behave(inp: Any) -> None:
        if down["on"]:
            raise ApplicationError("sandbox unreachable", type="SandboxUnavailable")  # retryable

    async def scenario(build: Build) -> tuple[SubSkillBuildStatus, SubSkillBuildResult, list[str]]:
        blocked = await build.until_waiting()
        down["on"] = False
        await build.retry()
        return blocked, await build.finish(), build.actions()

    blocked, result, actions = run(
        scenario,
        run_sandbox_tests=traced("run_sandbox_tests", sandbox, behave),
        build_manifest=traced("build_manifest", manifests),
        correct=traced("correct", corrections),
    )
    assert blocked.state.value == "TESTING_BLOCKED" and blocked.corrections_used == 0
    assert len(sandbox) == 3 + 1  # three infrastructure attempts, then the FDE's retry
    assert corrections == [] and len(manifests) == 1  # nothing but the sandbox tests re-ran
    assert result.certified
    assert actions == [
        "tests_started",
        "sandbox_unavailable",
        "sub_skill.testing_blocked",
        "retry",
        "tests_passed",
        "sub_skill.certified",
    ]


def test_e10_2_failing_tests_go_to_correction_not_testing_blocked() -> None:
    corrections: list[CorrectionInput] = []

    def behave(inp: Any) -> None:
        if inp.attempt == 0:
            raise ApplicationError(
                "tests failed", [str(FINDING)], type="TestsFailed", non_retryable=True
            )

    async def scenario(build: Build) -> tuple[SubSkillBuildResult, list[str]]:
        return await build.finish(), build.actions()

    result, actions = run(
        scenario,
        run_sandbox_tests=traced("run_sandbox_tests", [], behave),
        correct=traced("correct", corrections),
    )
    assert result.certified and corrections[0].failed_gate == "run_sandbox_tests"
    assert actions[:4] == ["tests_started", "tests_failed", "correction_applied", "tests_started"]


# --- T07: a crashed activity leaves one evidence row ---------------------------------------------


def test_e10_2_t07_retried_gate_activity_writes_one_row_per_key() -> None:
    gate_results: dict[tuple[uuid.UUID, int, int, int, str], uuid.UUID] = {}
    calls = {"n": 0}

    @activity.defn(name="ast_gate")
    async def crashing_gate(inp: BuildStageInput) -> IdResult:
        key = (inp.sub_skill_id, inp.build_no, inp.cycle, inp.attempt, "ast_gate")
        gate_results.setdefault(key, uuid.uuid4())  # upsert by the idempotency key
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("worker died after writing the row")  # retried by Temporal
        return IdResult(id=gate_results[key])

    async def scenario(build: Build) -> SubSkillBuildResult:
        return await build.finish()

    assert run(scenario, ast_gate=crashing_gate).certified
    assert calls["n"] == 2 and len(gate_results) == 1  # one row though the activity ran twice


# --- T08: missing library ------------------------------------------------------------------------


def test_e10_2_t08_missing_library_is_dependency_failed_then_one_correction() -> None:
    corrections: list[CorrectionInput] = []
    checks: list[BuildStageInput] = []

    def behave(inp: Any) -> Any:
        if inp.attempt == 0:
            return LibraryCheckResult(status=LibraryStatus.MISSING, finding_ids=[FINDING])
        return None

    async def scenario(build: Build) -> tuple[SubSkillBuildResult, list[str], SubSkillBuildStatus]:
        result = await build.finish()
        return result, build.actions(), await build.status()

    result, actions, status = run(
        scenario,
        check_libraries=traced("check_libraries", checks, behave),
        correct=traced("correct", corrections),
    )
    assert result.certified and status.corrections_used == 1
    assert len(corrections) == 1 and corrections[0].failed_gate == "check_libraries"
    assert actions[:2] == ["dependency_missing", "correction_applied"]
    assert [c.attempt for c in checks] == [0, 1]  # correction attempt 1


def test_e10_2_missing_library_never_fixed_waits_in_dependency_failed() -> None:
    def behave(inp: Any) -> LibraryCheckResult:
        return LibraryCheckResult(status=LibraryStatus.MISSING, finding_ids=[FINDING])

    async def scenario(build: Build) -> SubSkillBuildStatus:
        status = await build.until_waiting()
        await build.end()
        return status

    status = run(scenario, check_libraries=traced("check_libraries", [], behave))
    assert status.state.value == "DEPENDENCY_FAILED" and status.corrections_used == 2


# --- template error, cancel ----------------------------------------------------------------------


def test_e10_2_template_error_is_build_error_not_a_correction() -> None:
    corrections: list[CorrectionInput] = []

    def behave(inp: Any) -> None:
        raise ApplicationError("bad template", type="TemplateError", non_retryable=True)

    async def scenario(build: Build) -> tuple[SubSkillBuildResult, list[str], str | None]:
        return await build.finish(), build.actions(), build.state()

    result, actions, state = run(
        scenario,
        render_skeleton=traced("render_skeleton", [], behave),
        correct=traced("correct", corrections),
    )
    assert not result.certified and corrections == []
    assert state == "BUILD_ERROR" and actions == ["template_error", "sub_skill.build_error"]


def test_e10_2_cancel_signal_ends_a_waiting_build() -> None:
    async def scenario(build: Build) -> SubSkillBuildResult:
        await build.until_waiting()
        return await build.end()

    assert not run(scenario, ast_gate=traced("ast_gate", [], gate_failed)).certified


def test_e10_2_cancel_during_the_correction_call_stops_the_build() -> None:
    started = asyncio.Event()

    @activity.defn(name="correct")
    async def slow_correct(inp: CorrectionInput) -> IdResult:
        started.set()
        while True:  # heartbeats so the cancellation reaches it
            activity.heartbeat()
            await asyncio.sleep(0.1)

    async def scenario(build: Build) -> tuple[Exception, list[str]]:
        await started.wait()
        await build.handle.cancel()
        with pytest.raises(WorkflowFailureError) as raised:
            await build.finish()
        return raised.value, build.actions()

    error, actions = run(
        scenario, ast_gate=traced("ast_gate", [], gate_failed), correct=slow_correct
    )
    assert isinstance(error.__cause__, CancelledError)
    assert actions == ["gate_failed"]  # stopped in CORRECTING: nothing certified, nothing applied


# --- replay --------------------------------------------------------------------------------------


def test_e10_2_replays_from_recorded_history() -> None:
    """Determinism guard (TD §13.4): a change that breaks running builds fails here. The history
    covers a failing gate, two corrections, NEEDS_FDE_REVIEW, a retry and a certified second cycle.
    """
    workflow_id = build_workflow_id(uuid.UUID(int=0), 1)
    history = WorkflowHistory.from_json(workflow_id, json.loads(HISTORY.read_text("utf-8")))
    replayer = Replayer(workflows=[SubSkillBuild], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_sub_skill_build_history() -> None:  # pragma: no cover - maintenance helper
    broken = {"on": True}

    def behave(inp: Any) -> None:
        if broken["on"]:
            gate_failed()

    async def scenario(build: Build) -> dict[str, Any]:
        await build.until_waiting()
        broken["on"] = False
        await build.retry()
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    data = run(scenario, ast_gate=traced("ast_gate", [], behave))
    save_history(HISTORY, data)


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_cancelled_build_history() -> None:  # pragma: no cover - maintenance helper
    """E10.6: corrections exhausted, NEEDS_FDE_REVIEW, then the FDE cancels."""

    async def scenario(build: Build) -> dict[str, Any]:
        await build.until_waiting()
        await build.end()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    save_history(CANCELLED_HISTORY, run(scenario, ast_gate=traced("ast_gate", [], gate_failed)))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_testing_blocked_history() -> None:  # pragma: no cover - maintenance helper
    """E10.6: the sandbox is down (TESTING_BLOCKED), the FDE retries, the build certifies."""
    down = {"on": True}

    def behave(inp: Any) -> None:
        if down["on"]:
            raise ApplicationError("sandbox unreachable", type="SandboxUnavailable")

    async def scenario(build: Build) -> dict[str, Any]:
        await build.until_waiting()
        down["on"] = False
        await build.retry()
        await build.finish()
        data: dict[str, Any] = json.loads((await build.handle.fetch_history()).to_json())
        return data

    sandbox = traced("run_sandbox_tests", [], behave)
    save_history(TESTING_BLOCKED_HISTORY, run(scenario, run_sandbox_tests=sandbox))


def save_history(path: Path, data: dict[str, Any]) -> None:  # pragma: no cover - maintenance
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


# --- T05 / T09 through the API service on a real Temporal server ---------------------------------


def retry_call(build: Build, key: str, project: uuid.UUID | None = None) -> Any:
    """What POST .../sub-skills/{sub_id}/retry does after auth, against this server's client."""

    async def call() -> Any:
        status = await build.status()
        return await retry_build(
            build.handle._client,  # the handle's own client
            tenant_id=status.tenant_id,
            project_id=project or status.project_id,
            workflow_id=status.workflow_id,
            sub_skill_id=build.sub_skill,
            retry=SubSkillRetry(reason="fixed it", idempotency_key=key, actor_id=FDE),
        )

    return call()


def test_e10_2_t05_retry_while_building_is_409_not_in_review() -> None:
    release = asyncio.Event()

    @activity.defn(name="build_manifest")
    async def held(inp: BuildStageInput) -> IdResult:
        await release.wait()
        return IdResult(id=uuid.uuid4())

    async def scenario(build: Build) -> SubSkillBuildResult:
        with pytest.raises(NotInReview) as raised:
            await retry_call(build, "key-while-building")
        assert raised.value.status == 409 and raised.value.code.value == "NOT_IN_REVIEW"
        release.set()
        return await build.finish()

    assert run(scenario, build_manifest=held).certified  # nothing was signalled: it just ran


def test_e10_2_t09_same_idempotency_key_retries_once() -> None:
    async def scenario(build: Build) -> tuple[Any, Any, SubSkillBuildStatus, list[str]]:
        await build.until_waiting()
        first = await retry_call(build, "same-key-1")
        second = await retry_call(build, "same-key-1")
        waiting_again = await build.until_waiting(cycle=1)
        third = await retry_call(build, "same-key-1")  # a late repeat is still the same request
        actions = build.actions()
        await build.end()
        return (first, (second, third), waiting_again, actions)

    first, (second, third), waiting_again, actions = run(
        scenario, ast_gate=traced("ast_gate", [], gate_failed)
    )
    assert first == second == third and first.build_no == 1
    assert waiting_again.cycle == 1  # exactly one retry was processed
    assert actions.count("retry") == 1


def test_e10_2_retry_of_another_projects_build_is_404() -> None:
    async def scenario(build: Build) -> None:
        await build.until_waiting()
        with pytest.raises(ApiError) as raised:
            await retry_call(build, "key-other-project", project=uuid.uuid4())
        assert raised.value.status == 404
        await build.end()

    run(scenario, ast_gate=traced("ast_gate", [], gate_failed))


def test_e10_2_retry_of_an_unknown_sub_skill_is_404() -> None:
    async def scenario(build: Build) -> None:
        await build.finish()
        with pytest.raises(ApiError) as raised:
            await retry_build(
                build.handle._client,
                tenant_id=TENANT,
                project_id=uuid.uuid4(),
                workflow_id=uuid.uuid4(),
                sub_skill_id=uuid.uuid4(),
                retry=SubSkillRetry(reason="x", idempotency_key="key-unknown", actor_id=FDE),
            )
        assert raised.value.status == 404

    run(scenario)
