"""#767 on Temporal's time-skipping server: `RequestChangeApply` runs `apply_change_note`, signals
the waiting build only for a rebuild, tells the FDE either way, and is no stage of ProjectPipeline.
"""

import uuid
from typing import Any

from pipeline_db import actions, new_project
from sbam_contracts.request_change import (
    REQUEST_CHANGE_APPLY,
    ChangeNoteStatus,
    RequestChangeInput,
    RequestChangeResult,
    request_change_workflow_id,
)
from sbam_contracts.runs import TaskQueue
from temporalio import activity
from test_project_pipeline import TENANT, environment, run


def request_input(project: uuid.UUID, sub: uuid.UUID) -> RequestChangeInput:
    return RequestChangeInput(
        tenant_id=TENANT,
        project_id=project,
        run_id=uuid.uuid4(),
        correlation_id="cid-wg19",
        workflow_id=uuid.uuid4(),
        sub_skill_id=sub,
        note_id=uuid.uuid4(),
        build_workflow_id="build-none-1",
        actor_id="fde-1",
    )


def settled(status: ChangeNoteStatus, calls: list[RequestChangeInput]) -> Any:
    @activity.defn(name="apply_change_note")
    async def apply_change_note(inp: RequestChangeInput) -> RequestChangeResult:
        calls.append(inp)
        return RequestChangeResult(note_id=inp.note_id, status=status)

    return apply_change_note


def run_with(status: ChangeNoteStatus) -> tuple[list[RequestChangeInput], list[str]]:
    calls: list[RequestChangeInput] = []
    seen: list[str] = []

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, TENANT, state="IDLE")
        sub = uuid.uuid4()
        inp = request_input(project, sub)
        async with environment({"apply_change_note": settled(status, calls)}) as env:
            result = await env.client.execute_workflow(
                REQUEST_CHANGE_APPLY,
                inp,
                id=request_change_workflow_id(inp.workflow_id),
                task_queue=TaskQueue.LLM.value,
                result_type=RequestChangeResult,
            )
        assert result.status is status
        seen.extend(actions(sessions, "sub_skill", sub))

    run(scenario)
    return calls, seen


def test_wg19_a_held_patch_notifies_and_does_not_touch_the_build() -> None:
    calls, seen = run_with(ChangeNoteStatus.HELD_PATCH)
    assert len(calls) == 1 and seen == ["request_change.held_patch"]


def test_wg19_a_parked_note_notifies_the_fde() -> None:
    _, seen = run_with(ChangeNoteStatus.PARKED)
    assert seen == ["request_change.parked"]


def test_wg19_a_rebuild_tries_the_waiting_build_and_says_so_when_it_is_gone() -> None:
    _, seen = run_with(ChangeNoteStatus.REBUILDING)
    assert "request_change.rebuilding" in seen
