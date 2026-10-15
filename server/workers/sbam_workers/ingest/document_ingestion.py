"""`DocumentIngestion` workflow (TD §13.2), started by the upload post-finish hook (E4.2).

One execution per uploaded file, id `ingest-<document_version_id>` on sbam-ingest.

1. Scan (E4.3): `scan_clamav`, 3 attempts with 5 s x2 backoff. If every attempt fails the version
   is marked SCAN_FAILED (the FDE can retry) and this execution ends normally, so the pipeline goes
   on with the other documents. An infected file ends it the same way, as REJECTED.
2. Detect, parse and OCR follow (E4.4 to E4.8). Each must call `require_clean` first.

Each step keeps this workflow's name and its input and output types.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.documents import DOCUMENT_INGESTION, ScanStatus
    from sbam_contracts.pipeline import IdsResult, StageRef
    from sbam_contracts.runs import TaskQueue

    from sbam_workers.ingest.scan import PERMANENT, mark_scan_failed, scan_clamav
    from sbam_workers.registry import workflow_for

SCAN_ATTEMPTS = 3
SCAN_TIMEOUT = timedelta(minutes=2)
SCAN_RETRY = RetryPolicy(
    maximum_attempts=SCAN_ATTEMPTS,
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,  # waits of 5 s, then 10 s
    non_retryable_error_types=sorted(PERMANENT),
)


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=DOCUMENT_INGESTION)
class DocumentIngestion:
    @workflow.run
    async def run(self, inp: StageRef) -> IdsResult:
        try:
            outcome = await workflow.execute_activity(
                scan_clamav,
                inp,
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=SCAN_TIMEOUT,
                retry_policy=SCAN_RETRY,
            )
        except ActivityError as exc:
            if isinstance(exc.cause, ApplicationError) and exc.cause.type in PERMANENT:
                raise  # the version or its file is gone: nothing to mark
            outcome = await workflow.execute_activity(
                mark_scan_failed,
                inp,
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
        if outcome.verdict is not ScanStatus.CLEAN:
            return IdsResult(ids=[])  # infected or unscanned: nothing goes on to parsing
        return IdsResult(ids=[inp.ref_id])  # CLEAN: detect and parse follow (#70, #71, ...)
