"""#674 on Temporal's time-skipping server: a knowledge retry resumes ProjectPipeline where it
stopped.

The API (tests/api/test_project_workspace.py) moves KNOWLEDGE_FAILED -> KNOWLEDGE_BUILDING and
starts the pipeline with a `resume`; these check the workflow side of that resume (unchanged code).
"""

import uuid
from typing import Any

from pipeline_db import PROJECT_ROW, actions, new_project, state_of
from sbam_contracts.pipeline import (
    IdResult,
    PipelineStage,
    ResumeState,
    SowGenerateInput,
    StageRef,
)
from sbam_contracts.runs import RunInput
from temporalio import activity
from test_project_pipeline import TENANT, environment, run, start, until

SOW_VERSION = uuid.uuid4()


def recorders(calls: dict[str, list[Any]], built: uuid.UUID) -> dict[str, Any]:
    @activity.defn(name="ingest_documents")
    async def ingest(inp: Any) -> Any:
        calls["ingest"].append(inp)
        raise AssertionError("a knowledge retry never re-ingests")

    @activity.defn(name="build_knowledge")
    async def knowledge(inp: RunInput) -> IdResult:
        calls["knowledge"].append(inp)
        return IdResult(id=built)

    @activity.defn(name="build_okf_bundle")
    async def okf(inp: StageRef) -> IdResult:
        calls["okf"].append(inp.ref_id)
        return IdResult(id=inp.ref_id)  # a READY version comes back as it is

    @activity.defn(name="generate_sow")
    async def sow(inp: SowGenerateInput) -> IdResult:
        calls["sow"].append(inp.ref_id)
        return IdResult(id=SOW_VERSION)

    return {
        "ingest_documents": ingest,
        "build_knowledge": knowledge,
        "build_okf_bundle": okf,
        "generate_sow": sow,
    }


def test_e4_1b_t01_resumed_at_okf_with_a_ready_version_reaches_the_sow_review() -> None:
    ready = uuid.uuid4()
    calls: dict[str, list[Any]] = {"ingest": [], "knowledge": [], "okf": [], "sow": []}

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT, state="KNOWLEDGE_BUILDING")
        resume = ResumeState(stage=PipelineStage.OKF, refs={"knowledge": ready})
        async with environment(recorders(calls, uuid.uuid4())) as env:
            handle = await start(env, sessions, project, resume=resume)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await handle.terminate("test done")
        assert calls["ingest"] == [] and calls["knowledge"] == []  # nothing rebuilt
        assert calls["okf"] == [ready] and calls["sow"] == [ready]
        assert state_of(sessions, PROJECT_ROW, project) == "SOW_REVIEW"
        trail = actions(sessions, "project", project)
        assert "project_pipeline.ingestion_completed" not in trail

    run(scenario)


def test_e4_1b_t02_resumed_at_knowledge_builds_then_reaches_the_sow_review() -> None:
    built = uuid.uuid4()
    calls: dict[str, list[Any]] = {"ingest": [], "knowledge": [], "okf": [], "sow": []}

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT, state="KNOWLEDGE_BUILDING")
        resume = ResumeState(stage=PipelineStage.KNOWLEDGE)
        async with environment(recorders(calls, built)) as env:
            handle = await start(env, sessions, project, resume=resume)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await handle.terminate("test done")
        assert calls["ingest"] == []
        assert len(calls["knowledge"]) == 1
        assert calls["okf"] == [built] and calls["sow"] == [built]
        assert state_of(sessions, PROJECT_ROW, project) == "SOW_REVIEW"

    run(scenario)
