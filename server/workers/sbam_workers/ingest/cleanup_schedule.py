"""The `CleanupAbandonedUploads` Temporal Schedule (E4.12): daily at 03:00 UTC, created by the
ingest worker. Same pattern as the purge schedule (`purge_schedule.py`).

The time is a calendar spec, not a cron string: Temporal turns a cron string into calendars when the
schedule is read back, so a calendar is the form that round-trips and can be compared.
"""

import logging
from datetime import timedelta

from sbam_contracts.documents import CLEANUP_HOUR_UTC, CLEANUP_SCHEDULE_ID, CLEANUP_UPLOADS
from sbam_contracts.runs import TaskQueue
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from sbam_workers.registry import startup_for

log = logging.getLogger("sbam_workers.cleanup")


def cleanup_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            CLEANUP_UPLOADS,
            id=CLEANUP_SCHEDULE_ID,  # Temporal appends the start time to each scheduled run
            task_queue=TaskQueue.INGEST.value,
        ),
        # Minute, second and the other fields default to 0 / every: 03:00:00 UTC each day.
        spec=ScheduleSpec(calendars=[ScheduleCalendarSpec(hour=[ScheduleRange(CLEANUP_HOUR_UTC)])]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,  # a slow run is never doubled
            catchup_window=timedelta(days=1),  # a worker outage over 03:00 still runs it later
        ),
    )


@startup_for(TaskQueue.INGEST)
async def ensure_cleanup_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition. Idempotent: every
    ingest worker calls it on start. An operator's pause is kept."""
    wanted = cleanup_schedule()
    try:
        await client.create_schedule(CLEANUP_SCHEDULE_ID, wanted)
        log.info("created schedule %s (%02d:00 UTC)", CLEANUP_SCHEDULE_ID, CLEANUP_HOUR_UTC)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(CLEANUP_SCHEDULE_ID).update(converge)
