"""#533 `SweepDerivedFiles`: the workflow hands the tick's time to its activity and fails visibly
when a file could not be swept; the schedule is daily at 04:00 UTC and never overlaps."""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sbam_api.ingest.derived_sweep import SweepResult
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.ingest import derived_sweep as worker
from sbam_workers.ingest.derived_sweep import (
    SWEEP_HOUR_UTC,
    SWEEP_SCHEDULE_ID,
    SWEEP_WORKFLOW,
    SweepDerivedFiles,
    ensure_sweep_schedule,
    sweep_schedule,
)
from temporalio import activity
from temporalio.client import (
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    WorkflowFailureError,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


def run_workflow(result: SweepResult) -> tuple[Any, list[datetime]]:
    seen: list[datetime] = []

    @activity.defn(name="sweep_derived_files")
    def stand_in(as_of: datetime) -> SweepResult:
        seen.append(as_of)
        return result

    async def go() -> Any:
        from concurrent.futures import ThreadPoolExecutor

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env:
            async with Worker(
                env.client,
                task_queue=TaskQueue.INGEST.value,
                workflows=[SweepDerivedFiles],
                activities=[stand_in],
                activity_executor=ThreadPoolExecutor(2),
            ):
                return await env.client.execute_workflow(
                    SWEEP_WORKFLOW,
                    id=f"sweep-{uuid.uuid4()}",
                    task_queue=TaskQueue.INGEST.value,
                    result_type=SweepResult,
                )

    return asyncio.run(go()), seen


def test_a23d_the_workflow_passes_the_tick_time_and_returns_the_result() -> None:
    result, seen = run_workflow(SweepResult(scopes=3, listed=10, deleted=4))
    assert result.deleted == 4 and len(seen) == 1 and seen[0].tzinfo is not None


def test_a23d_a_run_that_could_not_delete_a_file_fails_visibly() -> None:
    with pytest.raises(WorkflowFailureError):
        run_workflow(SweepResult(failed=1))


def test_a23d_the_schedule_is_daily_after_the_purge_and_cleanup_and_never_overlaps() -> None:
    schedule = sweep_schedule()
    [calendar] = schedule.spec.calendars
    assert [r.start for r in calendar.hour] == [SWEEP_HOUR_UTC] and SWEEP_HOUR_UTC > 3
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
    action: Any = schedule.action
    assert action.workflow == SWEEP_WORKFLOW and action.task_queue == TaskQueue.INGEST.value
    assert schedule.policy.catchup_window == timedelta(days=1)


def test_a23d_registered_on_the_ingest_queue_only() -> None:
    registry.load_all()
    workflows, activities = registry.collect(TaskQueue.INGEST)
    assert SweepDerivedFiles in workflows and worker.sweep_derived in activities
    for queue in TaskQueue:
        if queue is not TaskQueue.INGEST:
            assert SweepDerivedFiles not in registry.collect(queue)[0]
    assert ensure_sweep_schedule in registry._STARTUP[TaskQueue.INGEST]


def test_a23d_a_second_worker_converges_the_existing_schedule() -> None:
    calls: list[str] = []

    class Handle:
        async def update(self, fn: Any) -> None:
            calls.append("update")

    class Client:
        def __init__(self, exists: bool) -> None:
            self.exists = exists

        async def create_schedule(self, schedule_id: str, schedule: Any) -> None:
            assert schedule_id == SWEEP_SCHEDULE_ID
            if self.exists:
                raise ScheduleAlreadyRunningError()
            calls.append("create")

        def get_schedule_handle(self, schedule_id: str) -> Handle:
            return Handle()

    asyncio.run(ensure_sweep_schedule(Client(False)))  # type: ignore[arg-type]
    asyncio.run(ensure_sweep_schedule(Client(True)))  # type: ignore[arg-type]
    assert calls == ["create", "update"]
