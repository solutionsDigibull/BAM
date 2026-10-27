"""`MasterDocumentIngestion` workflow (E3.8b, TD §13.2): parses one master document.

Started by the master-document attach API once the file is scanned CLEAN (id
`master-doc-<master_document_id>` on sbam-ingest). One step, `parse_master_document`, with the
same timeout and retry policy as an upload's parse: a file the loader cannot read is FAILED with its
code and the workflow ends normally (no retry: the answer would not change); other errors are
retried 3 times (10 s, doubling); after the last attempt, or past the 10-minute timeout,
`mark_master_document_failed` records PARSE_ERROR or TIMEOUT. The workflow returns the document's id
when it ended PARSED, nothing when it FAILED.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.api import ErrorCode
    from sbam_contracts.documents import (
        MASTER_DOCUMENT_INGESTION,
        PARSE_HEARTBEAT_SECONDS,
        PARSE_TIMEOUT_SECONDS,
    )
    from sbam_contracts.pipeline import IdsResult, StageRef
    from sbam_contracts.runs import TaskQueue

    from sbam_workers.ingest import master_documents
    from sbam_workers.ingest.parsing import ParseFailure
    from sbam_workers.registry import workflow_for

PARSE_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,  # waits of 10 s, then 20 s
    non_retryable_error_types=sorted(master_documents.PERMANENT),
)


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=MASTER_DOCUMENT_INGESTION)
class MasterDocumentIngestion:
    @workflow.run
    async def run(self, inp: StageRef) -> IdsResult:
        try:
            return await workflow.execute_activity(
                master_documents.parse_master_document,
                inp,
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(seconds=PARSE_TIMEOUT_SECONDS),
                heartbeat_timeout=timedelta(seconds=PARSE_HEARTBEAT_SECONDS),
                retry_policy=PARSE_RETRY,
            )
        except ActivityError as exc:
            if isinstance(exc.cause, ApplicationError) and exc.cause.type in (
                master_documents.PERMANENT
            ):
                raise  # the document or its file is gone, or it was never scanned: nothing to mark
            code = (
                ErrorCode.TIMEOUT if isinstance(exc.cause, TimeoutError) else ErrorCode.PARSE_ERROR
            )
            return await workflow.execute_activity(
                master_documents.mark_master_document_failed,
                ParseFailure(ref=inp, code=code),
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
