"""A-03 `RelaySowApprovals`: the workflow runs its activity with the tick's time and the schedule is
every minute, never doubled. The activity's work (`redeliver_pending`) is tested on SQLite
(tests/api) and real Postgres (tests/integration); here the activity is a stand-in of the same name.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.sow import approval_relay
from sbam_workers.sow.approval_relay import (
    RELAY_SCHEDULE_ID,
    RELAY_WORKFLOW,
    RelayResult,
    RelaySowApprovals,
    ensure_relay_schedule,
    relay_schedule,
)
from temporalio import activity
from temporalio.client import ScheduleAlreadyRunningError, ScheduleOverlapPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


def test_a03_the_workflow_passes_the_tick_time_to_the_activity() -> None:
    seen: list[datetime] = []

    @activity.defn(name="relay_sow_approvals")
    async def stand_in(as_of: datetime) -> RelayResult:
        seen.append(as_of)
        return RelayResult(sent=2, failed=1)

    async def run() -> RelayResult:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env:
            async with Worker(
                env.client,
                task_queue=TaskQueue.LLM.value,
                workflows=[RelaySowApprovals],
                activities=[stand_in],
            ):
                out: RelayResult = await env.client.execute_workflow(
                    RELAY_WORKFLOW,
                    id=f"relay-{uuid.uuid4()}",
                    task_queue=TaskQueue.LLM.value,
                    result_type=RelayResult,
                )
                return out

    result = asyncio.run(run())
    assert (result.sent, result.failed) == (2, 1)
    assert len(seen) == 1 and seen[0].tzinfo is not None


def test_a03_the_schedule_is_every_minute_and_never_overlaps() -> None:
    schedule = relay_schedule()
    [interval] = schedule.spec.intervals
    assert interval.every == timedelta(minutes=1)
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
    assert schedule.policy.catchup_window is not None
    assert schedule.policy.catchup_window <= timedelta(minutes=10)  # no backlog after an outage
    action: Any = schedule.action
    assert action.workflow == RELAY_WORKFLOW and action.task_queue == TaskQueue.LLM.value


def test_a03_the_relay_registers_itself_on_the_llm_queue_only() -> None:
    registry.load_all()
    workflows, activities = registry.collect(TaskQueue.LLM)
    assert RelaySowApprovals in workflows
    assert approval_relay.relay_sow_approvals in activities
    for queue in TaskQueue:
        if queue is not TaskQueue.LLM:
            other_workflows, other_activities = registry.collect(queue)
            assert RelaySowApprovals not in other_workflows
            assert approval_relay.relay_sow_approvals not in other_activities
    assert ensure_relay_schedule in registry._STARTUP[TaskQueue.LLM]


def test_a03_starting_a_second_worker_converges_the_existing_schedule() -> None:
    calls: list[str] = []

    class Handle:
        async def update(self, fn: Any) -> None:
            calls.append("update")

    class Client:
        def __init__(self, exists: bool) -> None:
            self.exists = exists

        async def create_schedule(self, schedule_id: str, schedule: Any) -> None:
            assert schedule_id == RELAY_SCHEDULE_ID
            if self.exists:
                raise ScheduleAlreadyRunningError()
            calls.append("create")

        def get_schedule_handle(self, schedule_id: str) -> Handle:
            return Handle()

    asyncio.run(ensure_relay_schedule(Client(exists=False)))  # type: ignore[arg-type]
    asyncio.run(ensure_relay_schedule(Client(exists=True)))  # type: ignore[arg-type]
    assert calls == ["create", "update"]
