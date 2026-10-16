"""E5.2 on Temporal's time-skipping server: the real ProjectPipeline and KnowledgeRebuild with the
real `build_version` (fake Cognee, SQLite knowledge world) in place of the stage stand-in."""

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from cognee_fake import EmbeddingException
from knowledge_db import World, add_document, add_project, versions
from pipeline_db import PROJECT_ROW, actions, new_project, state_of
from sbam_contracts.knowledge import KNOWLEDGE_REBUILD, knowledge_workflow_id
from sbam_contracts.pipeline import IdResult, PipelineStage
from sbam_contracts.runs import RunInput, TaskQueue
from sbam_workers.knowledge.build import build_version
from temporalio import activity
from temporalio.client import WorkflowFailureError
from test_project_pipeline import TENANT, environment, finish, run, start, until


def real_build(world: World) -> Any:
    @activity.defn(name="build_knowledge")
    async def build(inp: RunInput) -> IdResult:
        return IdResult(id=await build_version(world.deps, inp, attempt=activity.info().attempt))

    return build


def knowledge_world(tmp_path: Path, project: uuid.UUID) -> World:
    world = World(tmp_path)
    world.project = add_project(world.sessions, TENANT, project_id=project)
    return world


def docs(world: World, *texts: str) -> None:
    for text in texts:
        add_document(world.sessions, world.storage, world.project, text, tenant=TENANT)


def test_e5_2_t01_pipeline_builds_version_1_and_moves_on_to_the_sow(tmp_path: Path) -> None:
    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        world = knowledge_world(tmp_path, project)
        docs(world, "RFQ ZEBRA-7731", "BOM", "SOP")
        async with environment({"build_knowledge": real_build(world)}) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await handle.terminate("test done")
        [v1] = versions(world.sessions, project)
        assert (v1.version, v1.state) == (1, "READY")
        assert state_of(sessions, PROJECT_ROW, project) == "SOW_REVIEW"  # passed SOW_GENERATING

    run(scenario)


def test_e5_2_t03_cognee_down_fails_the_stage_and_no_sow_is_generated(tmp_path: Path) -> None:
    generated: list[Any] = []

    @activity.defn(name="generate_sow")
    async def sow(inp: Any) -> IdResult:
        generated.append(inp)
        return IdResult(id=uuid.uuid4())

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        world = knowledge_world(tmp_path, project)
        docs(world, "RFQ")
        world.cognee.fail = EmbeddingException("Bifrost unreachable")
        overrides = {"build_knowledge": real_build(world), "generate_sow": sow}
        async with environment(overrides) as env:
            handle = await start(env, sessions, project)
            await env.sleep(timedelta(minutes=10))  # 30 s backoff x2 over five attempts
            status = await finish(handle)
        assert status.failed_stage is PipelineStage.KNOWLEDGE
        assert status.last_error == "build_knowledge: CogneeUnavailable"
        assert state_of(sessions, PROJECT_ROW, project) == "KNOWLEDGE_FAILED"
        assert actions(sessions, "project", project)[-1] == "pipeline.failed"  # 'Rebuild' action
        [v1] = versions(world.sessions, project)
        assert (v1.state, v1.error_reason) == ("FAILED", "CogneeUnavailable")
        assert world.cognee.calls.count("ensure_dataset") >= 5  # every attempt reached Cognee

    run(scenario)
    assert generated == []


def test_e5_2_t04_empty_corpus_fails_without_retrying(tmp_path: Path) -> None:
    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        world = knowledge_world(tmp_path, project)
        docs(world, "RFQ")
        world.cognee.entities = False
        async with environment({"build_knowledge": real_build(world)}) as env:
            status = await finish(await start(env, sessions, project))
        assert status.last_error == "build_knowledge: EmptyCorpus"
        assert world.cognee.calls.count("cognify") == 1  # non-retryable: one attempt
        assert state_of(sessions, PROJECT_ROW, project) == "KNOWLEDGE_FAILED"

    run(scenario)


def test_e5_2_rebuild_workflow_builds_a_new_version_and_notifies(tmp_path: Path) -> None:
    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        world = knowledge_world(tmp_path, project)
        docs(world, "RFQ")
        inp = RunInput(
            tenant_id=TENANT, project_id=project, run_id=uuid.uuid4(), correlation_id="c-1"
        )
        async with environment({"build_knowledge": real_build(world)}) as env:
            result = await env.client.execute_workflow(
                KNOWLEDGE_REBUILD,
                inp,
                id=knowledge_workflow_id(project),
                task_queue=TaskQueue.KNOWLEDGE.value,
                result_type=IdResult,
            )
        [v1] = versions(world.sessions, project)
        assert (v1.id, v1.state) == (result.id, "READY")
        assert actions(sessions, "project", project) == ["knowledge.rebuilt"]
        assert state_of(sessions, PROJECT_ROW, project) == "IDLE"  # pipeline state untouched

    run(scenario)


def test_e5_2_rebuild_failure_notifies_and_fails_the_run(tmp_path: Path) -> None:
    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        world = knowledge_world(tmp_path, project)
        docs(world, "RFQ")
        world.cognee.entities = False
        inp = RunInput(
            tenant_id=TENANT, project_id=project, run_id=uuid.uuid4(), correlation_id="c-2"
        )
        async with environment({"build_knowledge": real_build(world)}) as env:
            try:
                await env.client.execute_workflow(
                    KNOWLEDGE_REBUILD,
                    inp,
                    id=knowledge_workflow_id(project),
                    task_queue=TaskQueue.KNOWLEDGE.value,
                )
                raise AssertionError("the rebuild should have failed")
            except WorkflowFailureError:
                pass
        assert actions(sessions, "project", project) == ["knowledge.rebuild_failed"]
        assert [v.state for v in versions(world.sessions, project)] == ["FAILED"]

    run(scenario)
