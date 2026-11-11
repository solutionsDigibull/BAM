"""`StartWaitingIngestions`: the workflow passes the tick's time to its activity, the schedule is
every minute and never doubled, and it registers on the ingest queue only."""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.ingest import start_waiting
from sbam_workers.ingest.start_waiting import (
    START_WAITING_SCHEDULE_ID,
    START_WAITING_WORKFLOW,
    StartWaitingIngestions,
    StartWaitingResult,
    start_waiting_schedule,
)
from temporalio import activity
from temporalio.client import ScheduleOverlapPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


def test_the_workflow_passes_the_tick_time_to_the_activity() -> None:
    seen: list[datetime] = []

    @activity.defn(name="start_waiting_ingestions")
    async def stand_in(as_of: datetime) -> StartWaitingResult:
        seen.append(as_of)
        return StartWaitingResult(started=2, failed=1)

    async def run() -> StartWaitingResult:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env:
            async with Worker(
                env.client,
                task_queue=TaskQueue.INGEST.value,
                workflows=[StartWaitingIngestions],
                activities=[stand_in],
            ):
                out: StartWaitingResult = await env.client.execute_workflow(
                    START_WAITING_WORKFLOW,
                    id=f"sw-{uuid.uuid4()}",
                    task_queue=TaskQueue.INGEST.value,
                    result_type=StartWaitingResult,
                )
                return out

    result = asyncio.run(run())
    assert (result.started, result.failed) == (2, 1)
    assert len(seen) == 1 and seen[0].tzinfo is not None


def test_the_schedule_is_every_minute_and_never_overlaps() -> None:
    schedule = start_waiting_schedule()
    [interval] = schedule.spec.intervals
    assert interval.every == timedelta(minutes=1)
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
    action: Any = schedule.action
    assert action.id == START_WAITING_SCHEDULE_ID
    assert action.workflow == START_WAITING_WORKFLOW and action.task_queue == "sbam-ingest"


def test_it_registers_itself_on_the_ingest_queue_only() -> None:
    registry.load_all()
    workflows, activities = registry.collect(TaskQueue.INGEST)
    assert StartWaitingIngestions in workflows
    assert start_waiting.start_waiting_ingestions in activities
    for queue in TaskQueue:
        if queue is not TaskQueue.INGEST:
            other_workflows, _ = registry.collect(queue)
            assert StartWaitingIngestions not in other_workflows
