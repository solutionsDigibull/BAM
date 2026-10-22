"""E10.1 ProjectPipeline against Temporal's time-skipping test server (no Docker).

Real workflow + stub activities; state transitions go through the real #41 service into SQLite
stand-in project/Workflow tables, so every transition, audit row and notification is checked.
"""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pipeline_db import (
    PROJECT_ROW,
    actions,
    mirror_row,
    new_project,
    pipeline_sessions,
    registered_stand_ins,
    state_of,
    workflow_rows,
)
from sbam_api.pipeline.service import (
    PipelineNotWaiting,
    pipeline_status,
    signal_pipeline,
    start_pipeline,
)
from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import (
    Cancel,
    Generate,
    IdResult,
    PipelineInput,
    PipelineStage,
    PipelineStatus,
    RebuildSubSkills,
    SowApproved,
    StageRef,
)
from sbam_contracts.runs import MirrorUpdate, RunInput, TaskQueue, pipeline_workflow_id
from sbam_workers import mirror, registry
from sbam_workers.pipeline.project_pipeline import ProjectPipeline
from sbam_workers.stub_ids import stub_id
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.client import Client, WorkflowHandle, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

TENANT = uuid.uuid4()
FDE = str(uuid.uuid4())
SHA = "ab" * 32
HISTORY = Path(__file__).parent / "histories" / "project_pipeline.json"
BEFORE_E5_2 = HISTORY.with_name("project_pipeline_before_e5_2.json")  # no heartbeat patch
BEFORE_E10_3 = HISTORY.with_name("project_pipeline_before_e10_3.json")  # no build scheduler patch
FULL_ORDER = [
    "ingest",
    "knowledge",
    "okf",
    "sow",
    "wait_sow_approval",
    "create_workflow",
    "extract_macros",
    "extract_sub_skills",
    "wait_generate",
    "build_schema_sql",
    "build_sub_skills",
    "certify_macros",
    "validate_workflow",
    "certify_workflow",
    "freeze",
    "done",
]
Overrides = dict[str, Callable[..., Any]]


@activity.defn(name="build_knowledge")
async def knowledge_stand_in(inp: RunInput) -> IdResult:
    """The real build (E5.2) needs Cognee; its own tests are tests/unit/test_knowledge_build.py.
    These tests drive the pipeline, so the stage answers like the stub it replaced."""
    return IdResult(id=stub_id(inp, "knowledge"))


@activity.defn(name="build_okf_bundle")
async def okf_stand_in(inp: StageRef) -> IdResult:
    """The real bundle (E5.3) reads Cognee's graph; its tests are tests/unit/test_okf_bundle.py."""
    return IdResult(id=stub_id(inp, "okf"))


@activity.defn(name="generate_sow")
async def sow_stand_in(inp: StageRef) -> IdResult:
    """The real generation (E6.2) reads the bundle and calls sbam-sow; its tests are
    tests/unit/test_sow_generate.py."""
    return IdResult(id=stub_id(inp, "sow"))


DEFAULT_OVERRIDES: "Overrides" = {
    "build_knowledge": knowledge_stand_in,
    "build_okf_bundle": okf_stand_in,
    "generate_sow": sow_stand_in,
}


def name_of(fn: Any) -> str:
    return str(fn.__temporal_activity_definition.name)


@asynccontextmanager
async def environment(
    overrides: Overrides | None = None, skip: set[TaskQueue] | None = None
) -> AsyncIterator[WorkflowEnvironment]:
    registry.load_all()
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    async with env, workers(env.client, {**DEFAULT_OVERRIDES, **(overrides or {})}, skip or set()):
        yield env


@asynccontextmanager
async def workers(
    client: Client, overrides: Overrides, skip: set[TaskQueue]
) -> AsyncIterator[None]:
    with ThreadPoolExecutor(max_workers=16) as executor:
        async with AsyncExitStack() as stack:
            for queue in TaskQueue:
                if queue in skip:
                    continue
                workflows, activities = registry.collect(queue)
                chosen = [overrides.get(name_of(a), a) for a in activities]
                worker = Worker(
                    client,
                    task_queue=queue.value,
                    workflows=workflows,
                    activities=chosen,
                    activity_executor=executor,
                )
                await stack.enter_async_context(worker)
            yield


def recording_mirror(stages: list[str]) -> Callable[..., Any]:
    @activity.defn(name="update_run_mirror")
    def record(update: MirrorUpdate) -> None:
        if not stages or stages[-1] != update.current_stage:
            stages.append(str(update.current_stage))
        mirror.update_run_mirror(update)

    return record


async def start(
    env: WorkflowEnvironment, sessions: sessionmaker[Session], project: uuid.UUID, **kw: Any
) -> WorkflowHandle[Any, Any]:
    pipeline_input = PipelineInput(
        tenant_id=TENANT,
        project_id=project,
        run_id=uuid.uuid4(),
        correlation_id="cid-e10-1",
        actor_id=FDE,
        search_attributes=False,
        **kw,
    )
    await start_pipeline(env.client, sessions, pipeline_input, with_search_attributes=False)
    return env.client.get_workflow_handle(pipeline_workflow_id(project), result_type=PipelineStatus)


async def until(handle: WorkflowHandle[Any, Any], stage: PipelineStage) -> PipelineStatus:
    """Poll the status query through the API helper (it retries a query that races a
    continue-as-new) until the pipeline reaches `stage`."""
    project = uuid.UUID(handle.id.removeprefix("pipeline-"))
    last = None
    for _ in range(600):
        status = await pipeline_status(handle._client, project)  # the handle's own client
        if status is not None and status.stage is stage:
            return status
        last = status.stage if status else None
        await asyncio.sleep(0.05)
    raise AssertionError(f"pipeline never reached {stage}; last {last}")


async def approve(handle: WorkflowHandle[Any, Any]) -> None:
    await handle.signal(
        "sow_approved", SowApproved(sow_version_id=uuid.uuid4(), sha256=SHA, actor_id=FDE)
    )


async def generate(handle: WorkflowHandle[Any, Any], key: str = "gen-1", **kw: Any) -> None:
    await handle.signal("generate", Generate(idempotency_key=key, actor_id=FDE, **kw))


async def finish(handle: WorkflowHandle[Any, Any]) -> PipelineStatus:
    result: PipelineStatus = await handle.result()
    return result


def run(scenario: Callable[[sessionmaker[Session]], Coroutine[Any, Any, Any]]) -> Any:
    sessions = pipeline_sessions()
    mirror.configure(sessions)
    with registered_stand_ins():
        return asyncio.run(scenario(sessions))


# --- T01 / T02: the whole pipeline ---------------------------------------------------------------


def test_e10_1_t01_end_to_end_reaches_frozen_in_order() -> None:
    stages: list[str] = []

    async def scenario(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, PipelineStatus]:
        project = new_project(sessions, TENANT)
        async with environment({"update_run_mirror": recording_mirror(stages)}) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            return project, await finish(handle)

    sessions_holder: dict[str, Any] = {}

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        sessions_holder["s"] = sessions
        return scenario(sessions)

    project, status = run(wrapped)
    sessions = sessions_holder["s"]
    assert stages == FULL_ORDER
    assert (
        status.stage is PipelineStage.DONE
        and status.sub_skills_done == status.sub_skills_total == 4
    )
    [wf] = workflow_rows(sessions, project)
    assert wf.state == "FROZEN"
    assert state_of(sessions, PROJECT_ROW, project) == "WORKFLOW_CREATED"
    assert actions(sessions, "project_pipeline", project) == [
        "start_processing",
        "ingestion_completed",
        "knowledge_ready",
        "sow_generated",
        "approve_sow",
        "workflow_created",
    ]
    assert actions(sessions, "workflow", wf.id) == [
        "sow_approved",
        "start_extraction",
        "extraction_completed",
        "generate",
        "all_sub_skills_certified",
        "workflow_certified",
        "freeze",
    ]
    row = mirror_row(sessions, project)
    assert (row.state, row.current_stage) == ("COMPLETED", "done")


def test_e10_1_t02_sow_approved_creates_exactly_one_workflow() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            assert state_of(sessions, PROJECT_ROW, project) == "SOW_REVIEW"
            await approve(handle)
            await approve(handle)  # double-click: ignored
            status = await until(handle, PipelineStage.WAIT_GENERATE)
            assert state_of(sessions, PROJECT_ROW, project) == "WORKFLOW_CREATED"
            [wf] = workflow_rows(sessions, project)
            assert wf.id == status.workflow_id and wf.state == "ISATVON_REVIEW"
            await handle.signal("cancel", Cancel(reason="test done", actor_id=FDE))
            await finish(handle)

    run(scenario)


def test_e10_1_sow_approved_before_the_wait_is_buffered() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            handle = await start(env, sessions, project)
            await approve(handle)  # arrives during ingest
            await until(handle, PipelineStage.WAIT_GENERATE)  # never stopped at the SOW wait
            await generate(handle)
            assert (await finish(handle)).stage is PipelineStage.DONE

    run(scenario)


# --- T04: duplicate generate ---------------------------------------------------------------------


def test_e10_1_t04_duplicate_generate_builds_once() -> None:
    builds: list[uuid.UUID] = []

    @activity.defn(name="build_manifest")  # the first stage of every SubSkillBuild (#107)
    async def counting_build(inp: BuildStageInput) -> IdResult:
        builds.append(inp.sub_skill_id)
        return IdResult(id=uuid.uuid4())

    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = new_project(sessions, TENANT)
        async with environment({"build_manifest": counting_build}) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle, "same-key")
            await generate(handle, "same-key")
            await finish(handle)

    run(scenario)
    assert len(builds) == 4 and len(set(builds)) == 4  # each Sub-Skill built once


# --- T05: worker crash ---------------------------------------------------------------------------


def test_e10_1_t05_worker_crash_during_extraction_resumes_without_duplicates() -> None:
    started = asyncio.Event()

    @activity.defn(name="extract_sub_skills")
    async def dying_extract(inp: Any) -> Any:
        started.set()
        await asyncio.Event().wait()  # the worker dies under it
        raise AssertionError("unreachable")

    async def scenario(sessions: sessionmaker[Session]) -> uuid.UUID:
        project = new_project(sessions, TENANT)
        others = set(TaskQueue) - {TaskQueue.LLM}
        async with environment(skip={TaskQueue.LLM}) as env:
            # LLM worker A: real stubs, except extract_sub_skills, during which it "dies".
            async with workers(
                env.client, {**DEFAULT_OVERRIDES, "extract_sub_skills": dying_extract}, others
            ):
                handle = await start(env, sessions, project)
                await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
                await approve(handle)
                await asyncio.wait_for(started.wait(), 60)
            # Worker A is gone; worker B (real stubs) picks up the retried activity.
            async with workers(env.client, DEFAULT_OVERRIDES, others):
                await until(handle, PipelineStage.WAIT_GENERATE)
                await generate(handle)
                await finish(handle)
        return project

    sessions_holder: dict[str, Any] = {}

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        sessions_holder["s"] = sessions
        return scenario(sessions)

    project = run(wrapped)
    sessions = sessions_holder["s"]
    [wf] = workflow_rows(sessions, project)  # one Workflow row
    workflow_actions = actions(sessions, "workflow", wf.id)
    assert workflow_actions.count("sow_approved") == 1
    assert workflow_actions.count("extraction_completed") == 1


# --- T06: cancel ---------------------------------------------------------------------------------


def test_e10_1_t06_cancel_during_building() -> None:
    @activity.defn(name="run_sandbox_tests")  # a long-running stage of SubSkillBuild (#107)
    async def slow_build(inp: BuildStageInput) -> IdResult:
        while True:  # heartbeats so the cancellation reaches it
            activity.heartbeat()
            await asyncio.sleep(0.1)

    async def scenario(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, PipelineStatus]:
        project = new_project(sessions, TENANT)
        async with environment({"run_sandbox_tests": slow_build}) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            await until(handle, PipelineStage.BUILD_SUB_SKILLS)
            await handle.signal("cancel", Cancel(reason="scope changed", actor_id=FDE))
            status = await finish(handle)
            return project, status

    sessions_holder: dict[str, Any] = {}

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        sessions_holder["s"] = sessions
        return scenario(sessions)

    project, status = run(wrapped)
    sessions = sessions_holder["s"]
    assert status.stage is PipelineStage.CANCELLED
    [wf] = workflow_rows(sessions, project)
    assert wf.state == "CANCELLED"
    assert "pipeline.cancelled" in actions(sessions, "project", project)
    assert mirror_row(sessions, project).state == "CANCELLED"


# --- T07: failure path ---------------------------------------------------------------------------


def test_e10_1_t07_sow_generation_failure_stops_the_pipeline() -> None:
    @activity.defn(name="generate_sow")
    async def broken_sow(inp: StageRef) -> IdResult:
        raise ApplicationError(
            "schema invalid", type="SchemaInvalidAfterRetries", non_retryable=True
        )

    async def scenario(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, PipelineStatus]:
        project = new_project(sessions, TENANT)
        async with environment({"generate_sow": broken_sow}) as env:
            handle = await start(env, sessions, project)
            return project, await finish(handle)

    sessions_holder: dict[str, Any] = {}

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        sessions_holder["s"] = sessions
        return scenario(sessions)

    project, status = run(wrapped)
    sessions = sessions_holder["s"]
    assert status.stage is PipelineStage.FAILED and status.failed_stage is PipelineStage.SOW
    assert status.last_error == "generate_sow: SchemaInvalidAfterRetries"
    assert state_of(sessions, PROJECT_ROW, project) == "SOW_GENERATION_FAILED"
    assert actions(sessions, "project", project) == ["pipeline.failed"]
    assert workflow_rows(sessions, project) == []
    row = mirror_row(sessions, project)
    assert (row.state, row.current_stage) == ("FAILED", "failed")


# --- T08: reminder -------------------------------------------------------------------------------


def test_e10_1_t08_one_reminder_after_three_days_still_waiting() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> uuid.UUID:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await env.sleep(timedelta(days=3, hours=1))
            await env.sleep(timedelta(days=4))
            status = await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            assert status.reminders_sent == [PipelineStage.WAIT_SOW_APPROVAL]
            assert status.waiting_for == "sow_approved"
            await handle.signal("cancel", Cancel(reason="test done", actor_id=FDE))
            await finish(handle)
        return project

    sessions_holder: dict[str, Any] = {}

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        sessions_holder["s"] = sessions
        return scenario(sessions)

    project = run(wrapped)
    sessions = sessions_holder["s"]
    assert actions(sessions, "project", project).count("pipeline.reminder") == 1


# --- needs review -> rebuild, continue-as-new, API helpers ---------------------------------------


def test_e10_1_sub_skill_needs_review_then_rebuild() -> None:
    attempts: dict[uuid.UUID, int] = {}

    @activity.defn(name="render_skeleton")  # a TemplateError ends a SubSkillBuild uncertified
    async def flaky_build(inp: BuildStageInput) -> IdResult:
        attempts[inp.sub_skill_id] = attempts.get(inp.sub_skill_id, 0) + 1
        if len(attempts) == 1 and attempts[inp.sub_skill_id] == 1:
            raise ApplicationError("template broke", type="TemplateError", non_retryable=True)
        return IdResult(id=uuid.uuid4())

    async def scenario(sessions: sessionmaker[Session]) -> uuid.UUID:
        project = new_project(sessions, TENANT)
        async with environment({"render_skeleton": flaky_build}) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            await until(handle, PipelineStage.WAIT_REBUILD)
            failed = [s for s, n in attempts.items() if n == 1][:1]
            rebuild = RebuildSubSkills(sub_skill_ids=failed, idempotency_key="rb-1", actor_id=FDE)
            await handle.signal("rebuild_subskills", rebuild)
            assert (await finish(handle)).stage is PipelineStage.DONE
        return project

    sessions_holder: dict[str, Any] = {}

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        sessions_holder["s"] = sessions
        return scenario(sessions)

    project = run(wrapped)
    sessions = sessions_holder["s"]
    [wf] = workflow_rows(sessions, project)
    flow = actions(sessions, "workflow", wf.id)
    assert flow[flow.index("generate") :] == [
        "generate",
        "sub_skill_needs_review",
        "retry",
        "all_sub_skills_certified",
        "workflow_certified",
        "freeze",
    ]


def test_e10_1_continue_as_new_keeps_state_and_mirror() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> uuid.UUID:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            handle = await start(env, sessions, project, continue_as_new_after=20)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            # The old run shows the wait stage while its `_enter` activity runs, then continues
            # as new: poll for the switch rather than racing it.
            first_run = mirror_row(sessions, project).temporal_run_id
            for _ in range(200):
                latest = await env.client.get_workflow_handle(handle.id).describe()
                if latest.run_id != first_run:
                    break
                await asyncio.sleep(0.05)
            assert latest.run_id != first_run  # it really continued as new
            await generate(handle)
            status = await finish(handle)
            assert status.stage is PipelineStage.DONE and status.workflow_id is not None
        return project

    sessions_holder: dict[str, Any] = {}

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        sessions_holder["s"] = sessions
        return scenario(sessions)

    project = run(wrapped)
    sessions = sessions_holder["s"]
    row = mirror_row(sessions, project)  # same mirror row across runs (first execution id)
    assert (row.state, row.current_stage) == ("COMPLETED", "done")
    assert len(workflow_rows(sessions, project)) == 1


def test_e10_1_signal_helper_refuses_when_not_waiting() -> None:
    async def scenario(sessions: sessionmaker[Session]) -> None:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            assert await pipeline_status(env.client, project) is None  # never started
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            gen = Generate(idempotency_key="early", actor_id=FDE)
            with pytest.raises(PipelineNotWaiting) as err:
                await signal_pipeline(
                    env.client, project, "generate", gen, expect_stage=PipelineStage.WAIT_GENERATE
                )
            assert (err.value.status, err.value.code) == (409, "PIPELINE_NOT_WAITING")
            sow = SowApproved(sow_version_id=uuid.uuid4(), sha256=SHA, actor_id=FDE)
            await signal_pipeline(
                env.client,
                project,
                "sow_approved",
                sow,
                expect_stage=PipelineStage.WAIT_SOW_APPROVAL,
            )
            await until(handle, PipelineStage.WAIT_GENERATE)
            await handle.signal("cancel", Cancel(reason="done", actor_id=FDE))
            await finish(handle)

    run(scenario)


# --- replay --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recorded",
    [HISTORY, BEFORE_E5_2, BEFORE_E10_3],
    ids=["current", "before_e5_2", "before_e10_3"],
)
def test_e10_1_replays_from_recorded_history(recorded: Path) -> None:
    """Determinism guard (TD §13.4): a change that breaks running pipelines fails here. E5.2-T09:
    `before_e5_2` was recorded before the knowledge stage's heartbeat patch, `current` after it.
    E10.3-T06: `before_e10_3` was recorded before the build scheduler patch."""
    workflow_id = pipeline_workflow_id(uuid.UUID(int=0))
    history = WorkflowHistory.from_json(workflow_id, json.loads(recorded.read_text("utf-8")))
    replayer = Replayer(workflows=[ProjectPipeline], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_project_pipeline_history() -> None:  # pragma: no cover - maintenance helper
    async def scenario(sessions: sessionmaker[Session]) -> dict[str, Any]:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            await finish(handle)
            data: dict[str, Any] = json.loads((await handle.fetch_history()).to_json())
            return data

    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(run(scenario), indent=1) + "\n", encoding="utf-8")


def test_e10_1_t06_signals_after_the_end_or_repeated_are_ignored() -> None:
    """Handler logic (no server): after the pipeline ended nothing is recorded; repeated keys and a
    second approval are ignored."""
    pipeline = ProjectPipeline()
    first = SowApproved(sow_version_id=uuid.uuid4(), sha256=SHA, actor_id=FDE)
    pipeline.on_sow_approved(first)
    pipeline.on_sow_approved(SowApproved(sow_version_id=uuid.uuid4(), sha256=SHA, actor_id=FDE))
    assert pipeline.sow_approved == first
    pipeline.on_rebuild(
        RebuildSubSkills(sub_skill_ids=[uuid.uuid4()], idempotency_key="k", actor_id=FDE)
    )
    pipeline.on_rebuild(
        RebuildSubSkills(sub_skill_ids=[uuid.uuid4()], idempotency_key="k", actor_id=FDE)
    )
    assert len(pipeline.rebuilds) == 1
    pipeline.ended = True
    pipeline.on_generate(Generate(idempotency_key="late", actor_id=FDE))
    pipeline.on_cancel(Cancel(reason="late", actor_id=FDE))
    assert pipeline.generate is None and pipeline.cancel_request is None
