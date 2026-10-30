"""#497 `SweepLlmDerivatives`: the workflow hands the tick's time to its activity; the schedule is
daily at 05:00 UTC (after the derived-file sweep) on the LLM queue and never overlaps."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from sbam_api.llm.derivative_sweep import SweepResult
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.ingest.derived_sweep import SWEEP_HOUR_UTC as DERIVED_SWEEP_HOUR
from sbam_workers.llm import derivative_sweep as worker
from sbam_workers.llm.derivative_sweep import (
    SWEEP_HOUR_UTC,
    SWEEP_SCHEDULE_ID,
    SWEEP_WORKFLOW,
    SweepLlmDerivatives,
    ensure_sweep_schedule,
    sweep_schedule,
)
from temporalio import activity
from temporalio.client import ScheduleAlreadyRunningError, ScheduleOverlapPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


def run_workflow(result: SweepResult) -> tuple[SweepResult, list[datetime]]:
    seen: list[datetime] = []

    @activity.defn(name="sweep_llm_derivatives")
    def stand_in(as_of: datetime) -> SweepResult:
        seen.append(as_of)
        return result

    async def go() -> SweepResult:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env:
            async with Worker(
                env.client,
                task_queue=TaskQueue.LLM.value,
                workflows=[SweepLlmDerivatives],
                activities=[stand_in],
                activity_executor=ThreadPoolExecutor(2),
            ):
                return await env.client.execute_workflow(  # type: ignore[no-any-return]
                    SWEEP_WORKFLOW,
                    id=f"sweep-{uuid.uuid4()}",
                    task_queue=TaskQueue.LLM.value,
                    result_type=SweepResult,
                )

    return asyncio.run(go()), seen


def test_a23c_the_workflow_passes_the_tick_time_and_returns_the_result() -> None:
    result, seen = run_workflow(SweepResult(ttl_days=90, deleted=4))
    assert result.deleted == 4 and len(seen) == 1 and seen[0].tzinfo is not None


def test_a23c_the_schedule_is_daily_after_the_file_sweep_and_never_overlaps() -> None:
    schedule = sweep_schedule()
    [calendar] = schedule.spec.calendars
    assert [r.start for r in calendar.hour] == [
        SWEEP_HOUR_UTC
    ] and SWEEP_HOUR_UTC > DERIVED_SWEEP_HOUR
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
    action: Any = schedule.action
    assert action.workflow == SWEEP_WORKFLOW and action.task_queue == TaskQueue.LLM.value
    assert schedule.policy.catchup_window == timedelta(days=1)


def test_a23c_registered_on_the_llm_queue_only() -> None:
    registry.load_all()
    workflows, activities = registry.collect(TaskQueue.LLM)
    assert SweepLlmDerivatives in workflows and worker.sweep_derivatives in activities
    for queue in TaskQueue:
        if queue is not TaskQueue.LLM:
            assert SweepLlmDerivatives not in registry.collect(queue)[0]
    assert ensure_sweep_schedule in registry._STARTUP[TaskQueue.LLM]


def test_a23c_a_second_worker_converges_the_existing_schedule() -> None:
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
