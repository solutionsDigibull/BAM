"""E6.3 / E6.2-T07 on Temporal's time-skipping server: `SowRegenerate` while the SOW is in review,
and ProjectPipeline resumed at its SOW stage after SOW_GENERATION_FAILED."""

import uuid
from typing import Any

import pytest
from pipeline_db import PROJECT_ROW, actions, new_project, state_of
from sbam_contracts.pipeline import IdResult, PipelineStage, ResumeState, SowGenerateInput
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sow_api import SOW_REGENERATE, sow_workflow_id
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from test_project_pipeline import TENANT, environment, run, start, until

VERSION = uuid.uuid4()


def regenerate_input(project: uuid.UUID, hint: str | None = "Add a QA module") -> SowGenerateInput:
    return SowGenerateInput(
        tenant_id=TENANT,
        project_id=project,
        run_id=uuid.uuid4(),
        correlation_id="cid-e6-3",
        ref_id=uuid.uuid4(),
        hint=hint,
    )


def recording(calls: list[SowGenerateInput], fail: bool = False) -> Any:
    @activity.defn(name="generate_sow")
    async def generate(inp: SowGenerateInput) -> IdResult:
        calls.append(inp)
        if fail:
            raise ApplicationError(
                "4 answers failed", type="SchemaInvalidAfterRetries", non_retryable=True
            )
        return IdResult(id=VERSION)

    return generate


def test_e6_2_t07_sow_regenerate_writes_a_version_and_leaves_the_pipeline_state() -> None:
    calls: list[SowGenerateInput] = []

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT, state="SOW_REVIEW")
        inp = regenerate_input(project)
        async with environment({"generate_sow": recording(calls)}) as env:
            result = await env.client.execute_workflow(
                SOW_REGENERATE,
                inp,
                id=sow_workflow_id(project),
                task_queue=TaskQueue.LLM.value,
                result_type=IdResult,
            )
        assert result.id == VERSION
        assert [c.hint for c in calls] == ["Add a QA module"]
        assert actions(sessions, "project", project) == ["sow.regenerated"]
        assert state_of(sessions, PROJECT_ROW, project) == "SOW_REVIEW"

    run(scenario)


def test_e6_3_sow_regenerate_failure_notifies_and_fails_the_run() -> None:
    calls: list[SowGenerateInput] = []

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT, state="SOW_REVIEW")
        async with environment({"generate_sow": recording(calls, fail=True)}) as env:
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    SOW_REGENERATE,
                    regenerate_input(project, hint=None),
                    id=sow_workflow_id(project),
                    task_queue=TaskQueue.LLM.value,
                    result_type=IdResult,
                )
        assert len(calls) == 1  # non-retryable: not asked again
        assert actions(sessions, "project", project) == ["sow.regeneration_failed"]
        assert state_of(sessions, PROJECT_ROW, project) == "SOW_REVIEW"

    run(scenario)


def test_e6_2_t07_pipeline_resumed_at_the_sow_stage_passes_the_hint_and_waits() -> None:
    calls: list[SowGenerateInput] = []
    okf = uuid.uuid4()

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT, state="SOW_GENERATING")
        resume = ResumeState(stage=PipelineStage.SOW, refs={"knowledge": okf, "okf": okf})
        async with environment({"generate_sow": recording(calls)}) as env:
            handle = await start(env, sessions, project, resume=resume, sow_hint="Shorter")
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await handle.terminate("test done")
        [call] = calls
        assert (call.ref_id, call.hint) == (okf, "Shorter")
        assert state_of(sessions, PROJECT_ROW, project) == "SOW_REVIEW"
        assert "project_pipeline.ingestion_completed" not in actions(sessions, "project", project)

    run(scenario)
