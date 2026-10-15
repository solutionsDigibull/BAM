"""The `PurgeDeleted` Temporal Schedule (E4.9): daily at 02:00 UTC, created by the ingest worker."""

import logging
from datetime import timedelta

from sbam_contracts.documents import PURGE_CRON, PURGE_DELETED, PURGE_SCHEDULE_ID
from sbam_contracts.runs import TaskQueue
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from sbam_workers.registry import startup_for

log = logging.getLogger("sbam_workers.purge")


def purge_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            PURGE_DELETED,
            id=PURGE_SCHEDULE_ID,  # Temporal appends the start time to each scheduled run
            task_queue=TaskQueue.INGEST.value,
        ),
        spec=ScheduleSpec(cron_expressions=[PURGE_CRON]),  # UTC unless a time zone is named
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,  # a slow run is never doubled
            catchup_window=timedelta(days=1),  # a worker outage over 02:00 still runs it later
        ),
    )


@startup_for(TaskQueue.INGEST)
async def ensure_purge_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition. Idempotent: every
    ingest worker calls it on start. An operator's pause is kept."""
    wanted = purge_schedule()
    try:
        await client.create_schedule(PURGE_SCHEDULE_ID, wanted)
        log.info("created schedule %s (%s UTC)", PURGE_SCHEDULE_ID, PURGE_CRON)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(PURGE_SCHEDULE_ID).update(converge)
