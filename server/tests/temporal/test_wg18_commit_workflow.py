"""#766 on Temporal's time-skipping server: `WorkflowCommit` runs recheck, freeze, register and
start Staging in that order, stops at a refusal, is completed by running it again (WG18-T05), and
is no stage of ProjectPipeline. WG18-T06: the recorded history replays (`test_replay_histories.py`
replays every file in `histories/`; `RECORD_HISTORY=1` re-records this one).
"""

import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pipeline_db import new_project
from sbam_contracts.pipeline import IdResult
from sbam_contracts.runs import TaskQueue
from sbam_contracts.workflow_commit import (
    WORKFLOW_COMMIT,
    StagingStartResult,
    StagingStartStatus,
    WorkflowCommitInput,
    WorkflowCommitResult,
    workflow_commit_id,
)
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from test_project_pipeline import TENANT, environment, run

HISTORY = Path(__file__).parent / "histories" / "workflow_commit.json"
DIGEST = "sha256:" + "a" * 64
HASH = "sha256:" + "b" * 64
REGISTRY_NAME = "wf_ledger_v1_0_stg"
ROOT = Path(__file__).resolve().parents[2] / "workers" / "sbam_workers"


def commit_input(project: uuid.UUID, workflow_id: uuid.UUID) -> WorkflowCommitInput:
    return WorkflowCommitInput(
        tenant_id=TENANT,
        project_id=project,
        run_id=uuid.uuid4(),
        correlation_id="cid-wg18",
        workflow_id=workflow_id,
        manifest_hash=HASH,
        actor_id="fde-1",
    )


class Steps:
    """The four activities as fakes that record their order. `failing` raises at that step."""

    def __init__(self, failing: str | None = None) -> None:
        self.calls: list[str] = []
        self.failing = failing

    def overrides(self) -> dict[str, Callable[..., Any]]:
        def plain(name: str) -> Callable[..., Any]:
            @activity.defn(name=name)
            async def step(inp: WorkflowCommitInput) -> IdResult:
                self.calls.append(name)
                if self.failing == name:
                    raise ApplicationError("refused", type="CommitRefused", non_retryable=True)
                return IdResult(id=inp.workflow_id)

            return step

        @activity.defn(name="commit_start_staging")
        async def start(inp: WorkflowCommitInput) -> WorkflowCommitResult:
            self.calls.append("commit_start_staging")
            return WorkflowCommitResult(
                workflow_id=inp.workflow_id,
                version="1.0.0",
                registry_name=REGISTRY_NAME,
                image_digest=DIGEST,
                manifest_hash=inp.manifest_hash,
                staging=StagingStartResult(status=StagingStartStatus.PENDING_RUNTIME, detail=None),
            )

        return {
            "commit_recheck": plain("commit_recheck"),
            "commit_freeze": plain("commit_freeze"),
            "commit_register": plain("commit_register"),
            "commit_start_staging": start,
        }


ORDER = ["commit_recheck", "commit_freeze", "commit_register", "commit_start_staging"]


def test_wg18_the_steps_run_in_order_and_the_result_carries_the_certified_digest() -> None:
    steps = Steps()

    async def scenario(sessions: Any) -> WorkflowCommitResult:
        project, workflow_id = new_project(sessions, TENANT, state="IDLE"), uuid.uuid4()
        async with environment(steps.overrides()) as env:
            result: WorkflowCommitResult = await env.client.execute_workflow(
                WORKFLOW_COMMIT,
                commit_input(project, workflow_id),
                id=workflow_commit_id(workflow_id),
                task_queue=TaskQueue.CERTIFY.value,
                result_type=WorkflowCommitResult,
            )
        return result

    result = run(scenario)
    assert steps.calls == ORDER
    assert result.image_digest == DIGEST and result.staging.status.value == "PENDING_RUNTIME"


def test_wg18_a_refusal_stops_the_run_before_anything_after_it() -> None:
    steps = Steps(failing="commit_freeze")

    async def scenario(sessions: Any) -> None:
        project, workflow_id = new_project(sessions, TENANT, state="IDLE"), uuid.uuid4()
        async with environment(steps.overrides()) as env:
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    WORKFLOW_COMMIT,
                    commit_input(project, workflow_id),
                    id=workflow_commit_id(workflow_id),
                    task_queue=TaskQueue.CERTIFY.value,
                    result_type=WorkflowCommitResult,
                )

    run(scenario)
    assert steps.calls == ["commit_recheck", "commit_freeze"]  # a refusal is not retried


def test_wg18_t05_a_stopped_commit_is_completed_by_running_it_again() -> None:
    steps = Steps(failing="commit_register")
    seen: list[list[str]] = []

    async def scenario(sessions: Any) -> None:
        project, workflow_id = new_project(sessions, TENANT, state="IDLE"), uuid.uuid4()
        inp = commit_input(project, workflow_id)
        async with environment(steps.overrides()) as env:
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    WORKFLOW_COMMIT,
                    inp,
                    id=workflow_commit_id(workflow_id),
                    task_queue=TaskQueue.CERTIFY.value,
                )
            seen.append(list(steps.calls))
            steps.failing = None  # the cause is gone; the FDE commits again
            await env.client.execute_workflow(
                WORKFLOW_COMMIT,
                inp,
                id=workflow_commit_id(workflow_id),
                task_queue=TaskQueue.CERTIFY.value,
                result_type=WorkflowCommitResult,
            )

    run(scenario)
    assert seen == [ORDER[:3]]
    assert steps.calls == [*ORDER[:3], *ORDER]  # the second run does every step, finishing the job


def test_wg18_the_commit_steps_are_not_stages_of_project_pipeline() -> None:
    pipeline = (ROOT / "pipeline" / "project_pipeline.py").read_text(encoding="utf-8")
    assert "commit_" not in pipeline and "WorkflowCommit" not in pipeline
    build = (ROOT / "pipeline" / "sub_skill_build.py").read_text(encoding="utf-8")
    assert "commit_" not in build and "WorkflowCommit" not in build


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_workflow_commit_history() -> None:  # pragma: no cover - maintenance helper
    steps = Steps()

    async def scenario(sessions: Any) -> dict[str, Any]:
        project, workflow_id = new_project(sessions, TENANT, state="IDLE"), uuid.uuid4()
        async with environment(steps.overrides()) as env:
            handle = await env.client.start_workflow(
                WORKFLOW_COMMIT,
                commit_input(project, workflow_id),
                id=workflow_commit_id(workflow_id),
                task_queue=TaskQueue.CERTIFY.value,
            )
            await handle.result()
            history = await handle.fetch_history()
            data: dict[str, Any] = json.loads(history.to_json())
            return data

    data = run(scenario)
    HISTORY.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
