"""E8.4 on Temporal's time-skipping server: `IsatvonRegenerate` runs `regenerate_skills`, tells the
FDE either way, and never moves the project's pipeline state."""

import uuid
from typing import Any

import pytest
from pipeline_db import PROJECT_ROW, actions, new_project, state_of
from sbam_contracts.isatvon_workbench import (
    ISATVON_REGENERATE,
    RegenerateInput,
    regenerate_workflow_id,
)
from sbam_contracts.pipeline import IdsResult
from sbam_contracts.runs import TaskQueue
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from test_project_pipeline import TENANT, environment, run

IDS = [uuid.uuid4(), uuid.uuid4()]


def regenerate_input(project: uuid.UUID, macro: uuid.UUID) -> RegenerateInput:
    return RegenerateInput(
        tenant_id=TENANT,
        project_id=project,
        run_id=uuid.uuid4(),
        correlation_id="cid-e8-4",
        workflow_id=uuid.uuid4(),
        macro_skill_id=macro,
        sub_skill_id=None,
        hint="Stricter",
        actor_id="fde-1",
    )


def recording(calls: list[RegenerateInput], fail: bool = False) -> Any:
    @activity.defn(name="regenerate_skills")
    async def regenerate_skills(inp: RegenerateInput) -> IdsResult:
        calls.append(inp)
        if fail:
            raise ApplicationError("no match", type="NoMatchingSubSkill", non_retryable=True)
        return IdsResult(ids=IDS)

    return regenerate_skills


def test_e8_4_t09_regenerate_runs_the_activity_and_notifies() -> None:
    calls: list[RegenerateInput] = []

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT, state="IDLE")
        macro = uuid.uuid4()
        inp = regenerate_input(project, macro)
        async with environment({"regenerate_skills": recording(calls)}) as env:
            result = await env.client.execute_workflow(
                ISATVON_REGENERATE,
                inp,
                id=regenerate_workflow_id(macro),
                task_queue=TaskQueue.LLM.value,
                result_type=IdsResult,
            )
        assert result.ids == IDS
        assert [c.hint for c in calls] == ["Stricter"]
        assert actions(sessions, "macro_skill", macro) == ["isatvon.regenerated"]
        assert state_of(sessions, PROJECT_ROW, project) == "IDLE"

    run(scenario)


def test_e8_4_t09_a_failed_regeneration_notifies_and_fails_the_run() -> None:
    calls: list[RegenerateInput] = []

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT, state="IDLE")
        macro = uuid.uuid4()
        async with environment({"regenerate_skills": recording(calls, fail=True)}) as env:
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    ISATVON_REGENERATE,
                    regenerate_input(project, macro),
                    id=regenerate_workflow_id(macro),
                    task_queue=TaskQueue.LLM.value,
                    result_type=IdsResult,
                )
        assert len(calls) == 1  # non-retryable: not asked again
        assert actions(sessions, "macro_skill", macro) == ["isatvon.regeneration_failed"]

    run(scenario)
