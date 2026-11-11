"""#735: a rebuild round refreshes the Workflow's schema.sql before it rebuilds.

Same harness as test_project_pipeline.py (time-skipping server, stub stages, SQLite stand-ins).
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from pipeline_db import new_project
from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import (
    IdResult,
    PipelineStage,
    RebuildSubSkills,
    WorkflowRef,
)
from sbam_contracts.runs import pipeline_workflow_id
from sbam_workers.pipeline.project_pipeline import ProjectPipeline
from sbam_workers.stub_ids import stub_id
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer
from test_project_pipeline import (
    FDE,
    TENANT,
    approve,
    environment,
    finish,
    generate,
    run,
    start,
    until,
)

BEFORE_735 = Path(__file__).parent / "histories" / "project_pipeline_rebuild_before_735.json"


def stages(fail_schema_refresh: bool = False) -> tuple[list[str], dict[str, Any]]:
    """The schema stage and a skeleton that fails once for the first Sub-Skill, both logging."""
    calls: list[str] = []
    builds: dict[uuid.UUID, int] = {}

    @activity.defn(name="build_schema_sql")
    async def schema(inp: WorkflowRef) -> IdResult:
        calls.append("build_schema_sql")
        if fail_schema_refresh and calls.count("build_schema_sql") > 1:
            raise ApplicationError("still invalid", type="SchemaInvalid", non_retryable=True)
        return IdResult(id=stub_id(inp, "schema_sql"))

    @activity.defn(name="render_skeleton")  # a TemplateError ends a SubSkillBuild uncertified
    async def skeleton(inp: BuildStageInput) -> IdResult:
        builds[inp.sub_skill_id] = builds.get(inp.sub_skill_id, 0) + 1
        calls.append(f"build {inp.sub_skill_id} #{builds[inp.sub_skill_id]}")
        if len(builds) == 1 and builds[inp.sub_skill_id] == 1:
            raise ApplicationError("template broke", type="TemplateError", non_retryable=True)
        return IdResult(id=uuid.uuid4())

    return calls, {"build_schema_sql": schema, "render_skeleton": skeleton, "_builds": builds}


def rebuild_once(overrides: dict[str, Any], history: bool = False) -> Any:
    builds = overrides.pop("_builds")

    async def scenario(sessions: sessionmaker[Session]) -> Any:
        project = new_project(sessions, TENANT)
        async with environment(overrides) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            await until(handle, PipelineStage.WAIT_REBUILD)
            failed = [s for s, n in builds.items() if n == 1][:1]
            rebuild = RebuildSubSkills(sub_skill_ids=failed, idempotency_key="rb-1", actor_id=FDE)
            await handle.signal("rebuild_subskills", rebuild)
            status = await finish(handle)
            if history:
                return json.loads((await handle.fetch_history()).to_json())
            return status

    return run(scenario)


def test_735_t03_a_rebuild_round_refreshes_the_schema_before_it_builds() -> None:
    calls, overrides = stages()
    assert rebuild_once(overrides).stage is PipelineStage.DONE
    assert calls.count("build_schema_sql") == 2  # the first build round, then the rebuild's
    refresh = calls.index("build_schema_sql", 1)
    rebuilt = [i for i, c in enumerate(calls) if c.endswith("#2")]
    assert rebuilt and refresh < rebuilt[0]  # refreshed first, then the Sub-Skill rebuilt


def test_735_t04_a_failed_refresh_still_rebuilds_with_the_last_passed_schema() -> None:
    calls, overrides = stages(fail_schema_refresh=True)
    assert rebuild_once(overrides).stage is PipelineStage.DONE
    assert calls.count("build_schema_sql") == 2
    assert any(c.endswith("#2") for c in calls)


def test_735_t05_a_rebuild_recorded_before_the_change_replays() -> None:
    """Determinism guard: a running pipeline that already had a rebuild round (no schema refresh
    in its history) replays; its next round is the first to refresh."""
    workflow_id = pipeline_workflow_id(uuid.UUID(int=0))
    history = WorkflowHistory.from_json(workflow_id, json.loads(BEFORE_735.read_text("utf-8")))
    replayer = Replayer(workflows=[ProjectPipeline], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_rebuild_history() -> None:  # pragma: no cover - maintenance helper
    """Recorded with the pipeline of before #735 (the file this test replays)."""
    _, overrides = stages()
    BEFORE_735.write_text(
        json.dumps(rebuild_once(overrides, history=True), indent=1) + "\n", encoding="utf-8"
    )
