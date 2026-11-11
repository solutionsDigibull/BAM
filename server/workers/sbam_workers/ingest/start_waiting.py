"""`StartWaitingIngestions`: hand documents that are waiting to Temporal, without a next upload.

A version is UPLOADED with `ingestion_started_at` empty when Temporal was down at upload, or after a
restore (the old run is closed, a new one is owed). Until now only the project's next upload started
it, so a restored document could sit at "scan waiting" for good. This Temporal Schedule (every
minute, created by an ingest worker on start, like the other schedules) starts those that have
waited over a minute. Starting is idempotent: the workflow id names the version.

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
    from sbam_api.ingest.ingestion import start_stale_waiting
    from sbam_api.ingest.repository import waiting_for_ingestion
    from sbam_api.temporal.client import connect
    from sbam_contracts.runs import TaskQueue

    from sbam_workers import mirror
    from sbam_workers.registry import activity_for, startup_for, workflow_for
    from sbam_workers.settings import WorkerSettings

START_WAITING_WORKFLOW = "StartWaitingIngestions"
START_WAITING_SCHEDULE_ID = "start-waiting-ingestions"
START_WAITING_EVERY = timedelta(minutes=1)
GRACE = timedelta(minutes=1)  # a fresh upload is left to its own request
log = logging.getLogger("sbam_workers.ingest.start_waiting")


class StartWaitingResult(BaseModel):
    started: int = 0
    failed: int = 0


@activity_for(TaskQueue.INGEST)
@activity.defn(name="start_waiting_ingestions")
async def start_waiting_ingestions(as_of: datetime) -> StartWaitingResult:
    settings = WorkerSettings()  # type: ignore[call-arg]
    sessions = mirror.sessions()
    # Look before connecting: most runs find nothing and need no Temporal connection.
    with sessions() as session:
        if not waiting_for_ingestion(session, as_of - GRACE, 1):
            return StartWaitingResult()
    client = await connect(settings.temporal_host, settings.temporal_namespace)
    started, failed = await start_stale_waiting(client, sessions, as_of - GRACE)
    log.info("waiting ingestions started: started=%s failed=%s", started, failed)
    return StartWaitingResult(started=started, failed=failed)


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=START_WAITING_WORKFLOW)
class StartWaitingIngestions:
    @workflow.run
    async def run(self) -> StartWaitingResult:
        result: StartWaitingResult = await workflow.execute_activity(
            start_waiting_ingestions,
            workflow.now(),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=5)),
        )
        return result  # one that could not be started is retried by the next tick


def start_waiting_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            START_WAITING_WORKFLOW,
            id=START_WAITING_SCHEDULE_ID,
            task_queue=TaskQueue.INGEST.value,
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=START_WAITING_EVERY)]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,  # a slow run is never doubled
            catchup_window=timedelta(minutes=5),  # after an outage, one run, not a backlog
        ),
    )


@startup_for(TaskQueue.INGEST)
async def ensure_start_waiting_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition. Idempotent: every
    ingest worker calls it on start. An operator's pause is kept."""
    wanted = start_waiting_schedule()
    try:
        await client.create_schedule(START_WAITING_SCHEDULE_ID, wanted)
        log.info("created schedule %s (every %s)", START_WAITING_SCHEDULE_ID, START_WAITING_EVERY)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(START_WAITING_SCHEDULE_ID).update(converge)
