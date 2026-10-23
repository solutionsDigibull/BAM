"""E7.1-T02 on Temporal's time-skipping server: the API's `signal_pipeline` reaches a
ProjectPipeline waiting for the approval, which then moves on to create the Workflow."""

import uuid
from typing import Any

import pytest
from pipeline_db import new_project
from sbam_api.pipeline.service import PipelineNotWaiting, signal_pipeline
from sbam_contracts.pipeline import PipelineStage, SowApproved
from temporalio.api.enums.v1 import EventType
from test_project_pipeline import FDE, TENANT, environment, run, start, until

SHA = "ab" * 32


def test_e7_1_t02_the_signal_reaches_the_waiting_pipeline_which_moves_on() -> None:
    version = uuid.uuid4()

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await signal_pipeline(
                env.client,
                project,
                "sow_approved",
                SowApproved(sow_version_id=version, sha256=SHA, actor_id=FDE),
                expect_stage=PipelineStage.WAIT_SOW_APPROVAL,
            )
            await until(handle, PipelineStage.WAIT_GENERATE)  # past create_workflow and extraction
            history = await handle.fetch_history()
            signals = [
                e.workflow_execution_signaled_event_attributes
                for e in history.events
                if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
            ]
            assert [s.signal_name for s in signals] == ["sow_approved"]
            assert str(version) in str(signals[0].input)
            await handle.terminate("test done")

    run(scenario)


def test_e7_1_a_pipeline_not_at_the_approval_wait_gets_no_signal() -> None:
    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT)
        async with environment() as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await handle.signal(
                "sow_approved", SowApproved(sow_version_id=uuid.uuid4(), sha256=SHA, actor_id=FDE)
            )
            await until(handle, PipelineStage.WAIT_GENERATE)
            with pytest.raises(PipelineNotWaiting):
                await signal_pipeline(
                    env.client,
                    project,
                    "sow_approved",
                    SowApproved(sow_version_id=uuid.uuid4(), sha256=SHA, actor_id=FDE),
                    expect_stage=PipelineStage.WAIT_SOW_APPROVAL,
                )
            await handle.terminate("test done")

    run(scenario)
