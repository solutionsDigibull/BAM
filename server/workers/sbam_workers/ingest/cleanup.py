"""`CleanupAbandonedUploads` (E4.12): the daily job that removes what unfinished uploads leave.

A Temporal Schedule starts it at 03:00 UTC, an hour after `PurgeDeleted`
(`cleanup_schedule.ensure_cleanup_schedule`, run when an ingest worker starts). The workflow is
deterministic: it reads the time from `workflow.now()` and gives it to the activity, which does all
the work (`cleanup_activities.run_cleanup`). A run that could not remove some file fails, so it is
visible; the next day's run tries again.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.documents import CLEANUP_UPLOADS, CleanupResult
    from sbam_contracts.runs import TaskQueue

    from sbam_workers.ingest.cleanup_activities import run_cleanup
    from sbam_workers.registry import workflow_for

CLEANUP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=3,
    non_retryable_error_types=["StorageNotConfigured"],
)


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=CLEANUP_UPLOADS)
class CleanupAbandonedUploads:
    @workflow.run
    async def run(self) -> CleanupResult:
        result: CleanupResult = await workflow.execute_activity(
            run_cleanup,
            workflow.now(),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=CLEANUP_RETRY,
        )
        if result.failed:
            raise ApplicationError(
                f"{result.failed} staged upload file(s) could not be removed; the next run retries",
                type="CleanupIncomplete",
                non_retryable=True,
            )
        return result
