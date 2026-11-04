"""#694 on Temporal's time-skipping server: a failed build_schema_sql returns the Workflow to
ISATVON_REVIEW and the Sub-Skills Generate sent to BUILDING back to VALID, names them in the
notification, and a run resumed at the Generate wait builds once the failure is fixed.

The API side (POST /pipeline/start resuming at wait_generate): tests/api/test_project_workspace.py.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from pipeline_db import WORKFLOW_ROW, actions, new_project, state_of, workflow_rows
from sbam_api.audit.models import AuditLog
from sbam_contracts.pipeline import (
    IdResult,
    IdsResult,
    IngestInput,
    PipelineStage,
    ResumeState,
    TransitionRequest,
    WorkflowRef,
)
from sbam_contracts.runs import pipeline_workflow_id
from sbam_contracts.state_machines import MachineName
from sbam_workers.pipeline.project_pipeline import ProjectPipeline
from sbam_workers.pipeline_state import apply_pipeline_transition
from sbam_workers.stub_ids import stub_id
from sqlalchemy import select
from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer
from test_project_pipeline import (
    TENANT,
    approve,
    environment,
    finish,
    generate,
    run,
    start,
    until,
)


@activity.defn(name="ingest_documents")
async def ingest_stand_in(inp: IngestInput) -> IdsResult:
    """The real ingestion parses in a rlimit-ed child (Linux only); this is about the build."""
    return IdsResult(ids=list(inp.document_version_ids))


@activity.defn(name="build_schema_sql")
async def invalid(inp: WorkflowRef) -> IdResult:
    raise ApplicationError(
        "schema.sql failed SCHEMA_SQL (FORBIDDEN_STATEMENT)",
        "schema-id",
        "FORBIDDEN_STATEMENT,UNKNOWN_TABLE",
        "MS04.1",
        type="SchemaInvalid",
        non_retryable=True,
    )


def test_694_t01_schema_failure_returns_workflow_and_sub_skills_and_names_them() -> None:
    sent: list[TransitionRequest] = []

    @activity.defn(name="apply_pipeline_transition")
    def recording(req: TransitionRequest) -> Any:
        sent.append(req)
        return apply_pipeline_transition(req)

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        overrides = {
            "ingest_documents": ingest_stand_in,
            "build_schema_sql": invalid,
            "apply_pipeline_transition": recording,
        }
        async with environment(overrides) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            status = await finish(handle)
        assert status.stage is PipelineStage.FAILED
        assert status.failed_stage is PipelineStage.BUILD_SCHEMA_SQL
        [wf] = workflow_rows(sessions, project)
        assert wf.state == "ISATVON_REVIEW"  # the workbench is editable again
        trail = actions(sessions, "workflow", wf.id)
        assert trail[-2:] == ["generate", "schema_failed"]
        aborted = [r for r in sent if r.event == "build_aborted"]
        assert len(aborted) == 4 and {r.machine for r in aborted} == {MachineName.SUB_SKILL}
        assert all(r.actor.value == "SYSTEM" for r in aborted)
        with sessions() as s:
            [note] = s.scalars(
                select(AuditLog).where(
                    AuditLog.action == "pipeline.failed", AuditLog.entity_id == str(project)
                )
            ).all()
        assert note.after is not None
        assert note.after["codes"] == "FORBIDDEN_STATEMENT,UNKNOWN_TABLE"
        assert note.after["sub_skills"] == "MS04.1"
        assert note.after["error"] == "SchemaInvalid"

    run(scenario)


def test_694_t02_a_run_resumed_at_wait_generate_builds_after_the_fix() -> None:
    calls = {"n": 0}

    @activity.defn(name="build_schema_sql")
    async def fixed_second_time(inp: WorkflowRef) -> IdResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ApplicationError(
                "bad", "id", "X", "MS01.1", type="SchemaInvalid", non_retryable=True
            )
        return IdResult(id=stub_id(inp, "schema_sql"))

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        async with environment(
            {"ingest_documents": ingest_stand_in, "build_schema_sql": fixed_second_time}
        ) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            await finish(handle)
            [wf] = workflow_rows(sessions, project)
            assert wf.state == "ISATVON_REVIEW"
            resume = ResumeState(
                stage=PipelineStage.WAIT_GENERATE,
                workflow_id=wf.id,
                sub_skill_ids=[uuid.uuid4() for _ in range(2)],
            )
            again = await start(env, sessions, project, resume=resume)
            await until(again, PipelineStage.WAIT_GENERATE)
            await generate(again, "gen-2")
            status = await finish(again)
        assert status.stage is PipelineStage.DONE
        assert state_of(sessions, WORKFLOW_ROW, wf.id) == "FROZEN"

    run(scenario)


HISTORY = Path(__file__).parent / "histories" / "project_pipeline_schema_failed.json"


def test_694_t03_replays_from_a_recorded_schema_failure() -> None:
    """A run that failed at build_schema_sql under this code replays (the patch marker and the
    schema_failed / build_aborted transitions are in the history). Runs recorded before the patch
    are covered by test_project_pipeline.py's `before_*` histories."""
    history = WorkflowHistory.from_json(
        pipeline_workflow_id(uuid.UUID(int=0)), json.loads(HISTORY.read_text("utf-8"))
    )
    replayer = Replayer(workflows=[ProjectPipeline], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_project_pipeline_schema_failed_history() -> None:  # pragma: no cover
    async def scenario(sessions: Any) -> dict[str, Any]:
        project = new_project(sessions, TENANT)
        async with environment(
            {"ingest_documents": ingest_stand_in, "build_schema_sql": invalid}
        ) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            await finish(handle)
            data: dict[str, Any] = json.loads((await handle.fetch_history()).to_json())
            return data

    HISTORY.write_text(json.dumps(run(scenario), indent=1) + "\n", encoding="utf-8")
