"""`RelaySowApprovals` (A-03): tell the pipeline about SOW approvals that were saved but not sent.

An approval commits before the pipeline is told (`sbam_api.sow.approve`). If Temporal failed in
between, the version is APPROVED with `signal_sent_at` NULL and the caller was answered 409/503;
most callers approve again, which sends it. This Temporal Schedule (every minute, created by an
LLM-queue worker on start, like the other schedules) sends it for the ones that do not. Versions
younger than a minute are left to their own request. Sending is idempotent: a version that is
already delivered, or whose Workflow exists, is never sent twice.

The workflow is deterministic; it hands `workflow.now()` to the activity so a retry judges the
same moment.
"""

import logging
from datetime import datetime, timedelta

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel
    from sbam_api.sow.approve import redeliver_pending
    from sbam_api.temporal.client import connect
    from sbam_contracts.runs import TaskQueue

    from sbam_workers import mirror
    from sbam_workers.registry import activity_for, startup_for, workflow_for
    from sbam_workers.settings import WorkerSettings

RELAY_WORKFLOW = "RelaySowApprovals"
RELAY_SCHEDULE_ID = "relay-sow-approvals"
RELAY_EVERY = timedelta(minutes=1)
log = logging.getLogger("sbam_workers.sow.approval_relay")


class RelayResult(BaseModel):
    sent: int = 0
    failed: int = 0


@activity_for(TaskQueue.LLM)
@activity.defn(name="relay_sow_approvals")
async def relay_sow_approvals(as_of: datetime) -> RelayResult:
    settings = WorkerSettings()  # type: ignore[call-arg]
    cached: list[Client] = []

    async def get_client() -> Client:
        if not cached:  # one connection per run, only when there is something to send
            cached.append(await connect(settings.temporal_host, settings.temporal_namespace))
        return cached[0]

    sent, failed = await redeliver_pending(mirror.sessions(), get_client, as_of)
    if sent or failed:
        log.info("sow approvals relayed: sent=%s failed=%s", sent, failed)
    return RelayResult(sent=sent, failed=failed)


@workflow_for(TaskQueue.LLM)
@workflow.defn(name=RELAY_WORKFLOW)
class RelaySowApprovals:
    @workflow.run
    async def run(self) -> RelayResult:
        result: RelayResult = await workflow.execute_activity(
            relay_sow_approvals,
            workflow.now(),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=5)),
        )
        return result  # a project that could not be told is retried by the next tick


def relay_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            RELAY_WORKFLOW, id=RELAY_SCHEDULE_ID, task_queue=TaskQueue.LLM.value
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=RELAY_EVERY)]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,  # a slow run is never doubled
            catchup_window=timedelta(minutes=5),  # after an outage, one run, not a backlog
        ),
    )


@startup_for(TaskQueue.LLM)
async def ensure_relay_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition. Idempotent: every
    LLM worker calls it on start. An operator's pause is kept."""
    wanted = relay_schedule()
    try:
        await client.create_schedule(RELAY_SCHEDULE_ID, wanted)
        log.info("created schedule %s (every %s)", RELAY_SCHEDULE_ID, RELAY_EVERY)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(RELAY_SCHEDULE_ID).update(converge)
