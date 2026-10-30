"""`SweepDerivedFiles` (#533): the daily job that removes derived files no row references.

A Temporal Schedule starts it at 04:00 UTC, after the purge (02:00) and the upload clean-up (03:00),
created by the ingest worker on start. The workflow is deterministic: it hands `workflow.now()` to
the activity, which does the work (`sbam_api.ingest.derived_sweep`). A run that could not delete a
file fails, so it is visible; the next day's run tries again.
"""

import logging
from datetime import datetime, timedelta

from temporalio import activity, workflow
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
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from sbam_api.ingest.derived_sweep import SweepResult, sweep_derived_files
    from sbam_api.storage.base import ListableStorage
    from sbam_api.storage.factory import make_backend
    from sbam_contracts.runs import TaskQueue

    from sbam_workers import mirror
    from sbam_workers.registry import activity_for, startup_for, workflow_for
    from sbam_workers.settings import WorkerSettings

SWEEP_WORKFLOW = "SweepDerivedFiles"
SWEEP_SCHEDULE_ID = "sweep-derived-files"
SWEEP_HOUR_UTC = 4
log = logging.getLogger("sbam_workers.derived_sweep")

_storage: ListableStorage | None = None


def configure(storage: ListableStorage | None) -> None:
    """Use this storage (tests). Otherwise the one the worker settings name."""
    global _storage
    _storage = storage


def _backend() -> ListableStorage:
    if _storage is not None:
        return _storage
    try:
        backend = make_backend(WorkerSettings())  # type: ignore[call-arg]
    except ValueError as exc:  # no STORAGE_ROOT / S3_BUCKET: retrying cannot fix it
        raise ApplicationError(str(exc), type="StorageNotConfigured", non_retryable=True) from exc
    if not hasattr(backend, "list_keys"):
        raise ApplicationError(
            "this storage backend cannot list its objects",
            type="StorageNotListable",
            non_retryable=True,
        )
    return backend  # type: ignore[return-value]


@activity_for(TaskQueue.INGEST)
@activity.defn(name="sweep_derived_files")
def sweep_derived(as_of: datetime) -> SweepResult:
    """`as_of` is the workflow clock, so a retry judges the same moment."""
    correlation = f"derived-sweep-{activity.info().workflow_run_id}"
    with mirror.sessions()() as session:
        return sweep_derived_files(session, _backend(), as_of, correlation)


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=SWEEP_WORKFLOW)
class SweepDerivedFiles:
    @workflow.run
    async def run(self) -> SweepResult:
        result: SweepResult = await workflow.execute_activity(
            sweep_derived,
            workflow.now(),
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(minutes=5),
                maximum_attempts=3,
                non_retryable_error_types=["StorageNotConfigured", "StorageNotListable"],
            ),
        )
        if result.failed:
            raise ApplicationError(
                f"{result.failed} derived file(s) could not be swept; the next run retries",
                type="SweepIncomplete",
                non_retryable=True,
            )
        return result


def sweep_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            SWEEP_WORKFLOW, id=SWEEP_SCHEDULE_ID, task_queue=TaskQueue.INGEST.value
        ),
        spec=ScheduleSpec(calendars=[ScheduleCalendarSpec(hour=[ScheduleRange(SWEEP_HOUR_UTC)])]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,  # a slow run is never doubled
            catchup_window=timedelta(days=1),
        ),
    )


@startup_for(TaskQueue.INGEST)
async def ensure_sweep_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition (idempotent)."""
    wanted = sweep_schedule()
    try:
        await client.create_schedule(SWEEP_SCHEDULE_ID, wanted)
        log.info("created schedule %s (%02d:00 UTC)", SWEEP_SCHEDULE_ID, SWEEP_HOUR_UTC)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(SWEEP_SCHEDULE_ID).update(converge)
