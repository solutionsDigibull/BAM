"""#133 on Temporal's time-skipping server: `ExportVersion` runs its one activity and returns what
it stored; a refusal (HashMismatch, non-retryable) fails the run after one attempt and is not
retried; a transient failure is retried. The recorded history replays (`test_replay_histories.py`
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
from sbam_contracts.exports import (
    EXPORT_ACTIVITY,
    EXPORT_VERSION,
    ExportInput,
    ExportResult,
    export_workflow_id,
)
from sbam_contracts.runs import TaskQueue
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from test_project_pipeline import TENANT, environment, run

HISTORY = Path(__file__).parent / "histories" / "export_version.json"
SHA = "c" * 64
ROOT = Path(__file__).resolve().parents[2] / "workers" / "sbam_workers"


def export_input(project: uuid.UUID) -> ExportInput:
    return ExportInput(
        tenant_id=TENANT,
        project_id=project,
        run_id=uuid.uuid4(),
        correlation_id="cid-e14-5",
        workflow_id=uuid.uuid4(),
        version="1.0.0",
        actor_id="fde-1",
    )


class Export:
    """The activity as a fake. `fail` is raised on the attempts listed in `failing_attempts`."""

    def __init__(self, fail: Exception | None = None, failing_attempts: int = 0) -> None:
        self.attempts = 0
        self.fail = fail
        self.failing_attempts = failing_attempts

    def overrides(self) -> dict[str, Callable[..., Any]]:
        @activity.defn(name=EXPORT_ACTIVITY)
        async def export(inp: ExportInput) -> ExportResult:
            self.attempts += 1
            if self.fail is not None and self.attempts <= self.failing_attempts:
                raise self.fail
            return ExportResult(
                workflow_id=inp.workflow_id,
                version=inp.version,
                storage_key=f"tenant/exports/{SHA}",
                sha256=SHA,
                size=1024,
                entries=12,
            )

        return {EXPORT_ACTIVITY: export}


async def _execute(sessions: Any, fake: Export) -> ExportResult:
    project = new_project(sessions, TENANT, state="IDLE")
    inp = export_input(project)
    async with environment(fake.overrides()) as env:
        result: ExportResult = await env.client.execute_workflow(
            EXPORT_VERSION,
            inp,
            id=export_workflow_id(inp.run_id),
            task_queue=TaskQueue.CERTIFY.value,
            result_type=ExportResult,
        )
    return result


def test_e14_5_export_version_returns_what_the_activity_stored() -> None:
    fake = Export()

    async def scenario(sessions: Any) -> ExportResult:
        return await _execute(sessions, fake)

    result = run(scenario)
    assert fake.attempts == 1
    assert result.sha256 == SHA and result.version == "1.0.0" and result.entries == 12


def test_e14_5_t07_a_hash_mismatch_fails_the_run_without_a_retry() -> None:
    fake = Export(
        ApplicationError("HashMismatch: tampered", type="HashMismatch", non_retryable=True), 99
    )

    async def scenario(sessions: Any) -> None:
        with pytest.raises(WorkflowFailureError):
            await _execute(sessions, fake)

    run(scenario)
    assert fake.attempts == 1


def test_e14_5_a_transient_failure_is_retried() -> None:
    fake = Export(RuntimeError("storage briefly unavailable"), 2)

    async def scenario(sessions: Any) -> ExportResult:
        return await _execute(sessions, fake)

    result = run(scenario)
    assert fake.attempts == 3 and result.sha256 == SHA


def test_e14_5_export_is_no_stage_of_project_pipeline() -> None:
    for name in ("project_pipeline.py", "sub_skill_build.py"):
        source = (ROOT / "pipeline" / name).read_text(encoding="utf-8")
        assert EXPORT_ACTIVITY not in source and EXPORT_VERSION not in source


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_export_version_history() -> None:  # pragma: no cover - maintenance helper
    fake = Export()

    async def scenario(sessions: Any) -> dict[str, Any]:
        project = new_project(sessions, TENANT, state="IDLE")
        inp = export_input(project)
        async with environment(fake.overrides()) as env:
            handle = await env.client.start_workflow(
                EXPORT_VERSION,
                inp,
                id=export_workflow_id(inp.run_id),
                task_queue=TaskQueue.CERTIFY.value,
            )
            await handle.result()
            history = await handle.fetch_history()
            data: dict[str, Any] = json.loads(history.to_json())
            return data

    data = run(scenario)
    HISTORY.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
