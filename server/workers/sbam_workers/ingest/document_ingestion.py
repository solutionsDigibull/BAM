"""`DocumentIngestion` workflow (TD §13.2), started by the upload post-finish hook (E4.2).

One execution per uploaded file, id `ingest-<document_version_id>` on sbam-ingest.

1. Scan (E4.3): `scan_clamav`, 3 attempts with 5 s x2 backoff. If every attempt fails the version
   is marked SCAN_FAILED (the FDE can retry) and this execution ends normally, so the pipeline goes
   on with the other documents. An infected file ends it the same way, as REJECTED.
2. Detect (E4.4): `detect_type` checks the content against the extension and the enabled Filetype
   rows. A mismatch, an unsupported type or a bad encoding ends the execution as REJECTED.
3. Parse (E4.5): `parse_document` turns the version into `doc.md` and `doc.json`. A file the loader
   cannot read is PARSE_FAILED with its code and this execution ends normally (no retry: the answer
   would not change). Other errors are retried 3 times (10 s, doubling); after the last attempt, or
   past the 10-minute timeout, `mark_parse_failed` records PARSE_ERROR or TIMEOUT. A type with no
   loader yet stays PARSING.
5. Attachments (E4.6): after a PARSED e-mail, `register_attachments` makes a child document for each
   attachment and a child `DocumentIngestion` runs for each; `finish_attachments` then notes on the
   e-mail's report any child that was rejected or failed. The e-mail stays PARSED.
4. OCR (E4.8): when the parse leaves a version PARSING with pages in `ocr_needed_pages` (a scan, an
   image), `ocr_document` reads those pages with the vision model, 3 attempts (10 s, doubling),
   30 minutes at most with a 60 s heartbeat. After the last attempt, or at the timeout,
   `mark_ocr_failed` makes the pages unreadable (OCR_UNAVAILABLE or TIMEOUT). Each step must call
   `require_clean` first.

Each step keeps this workflow's name and its input and output types.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, TimeoutError
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.api import ErrorCode
    from sbam_contracts.documents import (
        DOCUMENT_INGESTION,
        OCR_HEARTBEAT_SECONDS,
        OCR_TIMEOUT_SECONDS,
        PARSE_HEARTBEAT_SECONDS,
        PARSE_TIMEOUT_SECONDS,
        ParseOutcome,
        ScanStatus,
    )
    from sbam_contracts.pipeline import IdsResult, StageRef
    from sbam_contracts.runs import TaskQueue, ingest_workflow_id

    from sbam_workers.ingest import attachments, ocr, parsing, type_detection
    from sbam_workers.ingest.scan import PERMANENT, mark_scan_failed, scan_clamav
    from sbam_workers.registry import workflow_for

DETECT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=2),
    non_retryable_error_types=[type_detection.NOT_FOUND, type_detection.NOT_DETECTABLE],
)
PARSE_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,  # waits of 10 s, then 20 s
    non_retryable_error_types=sorted(parsing.PERMANENT),
)
OCR_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,  # waits of 10 s, then 20 s
    non_retryable_error_types=sorted(parsing.PERMANENT),
)
ATTACHMENT_RETRY = RetryPolicy(
    maximum_attempts=3,
    initial_interval=timedelta(seconds=5),
    non_retryable_error_types=[attachments.NOT_FOUND, "NotScanned"],
)
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
        if workflow.patched("e4-4-detect-type"):  # runs started before E4.4 skip it on replay
            detected = await workflow.execute_activity(
                type_detection.detect_type,
                inp,
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DETECT_RETRY,
            )
            if not detected.ids or not workflow.patched("e4-5-parse"):  # started before E4.5
                return detected
            result = await self._parse(inp)
            if result.ids and workflow.patched("e4-6-attachments"):  # started before E4.6
                await self._attachments(inp)
            return result
        return IdsResult(ids=[inp.ref_id])  # CLEAN: parse follows (#71, ...)

    async def _parse(self, inp: StageRef) -> IdsResult:
        try:
            outcome = await workflow.execute_activity(
                parsing.parse_document,
                inp,
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(seconds=PARSE_TIMEOUT_SECONDS),
                heartbeat_timeout=timedelta(seconds=PARSE_HEARTBEAT_SECONDS),
                retry_policy=PARSE_RETRY,
            )
        except ActivityError as exc:
            if isinstance(exc.cause, ApplicationError) and exc.cause.type in parsing.PERMANENT:
                raise  # the version or its file is gone, or it was never scanned: nothing to mark
            code = (
                ErrorCode.TIMEOUT if isinstance(exc.cause, TimeoutError) else ErrorCode.PARSE_ERROR
            )
            outcome = await workflow.execute_activity(
                parsing.mark_parse_failed,
                parsing.ParseFailure(ref=inp, code=code),
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
        if _needs_ocr(outcome) and workflow.patched("e4-8-ocr"):  # started before E4.8: no OCR
            outcome = await self._ocr(inp)
        # Failed: nothing goes on. Parsed, or no loader for the type yet: the version is handed on.
        return IdsResult(ids=[inp.ref_id] if outcome.parsed or outcome.code is None else [])

    async def _attachments(self, inp: StageRef) -> None:
        """E-mail attachments (E4.6): each becomes a child document with its own `DocumentIngestion`
        (scan, type check, parse). The parent is already PARSED and stays so: this only waits for
        the children, then writes what became of them on the parent's report."""
        children = await workflow.execute_activity(
            attachments.register_attachments,
            inp,
            task_queue=TaskQueue.INGEST.value,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=ATTACHMENT_RETRY,
        )
        if not children.ids:
            return
        await asyncio.gather(
            *(
                workflow.execute_child_workflow(
                    DocumentIngestion.run,
                    StageRef(
                        tenant_id=inp.tenant_id,
                        project_id=inp.project_id,
                        run_id=workflow.uuid4(),
                        correlation_id=inp.correlation_id,
                        ref_id=child,
                    ),
                    id=ingest_workflow_id(child),
                    task_queue=TaskQueue.INGEST.value,
                    parent_close_policy=ParentClosePolicy.ABANDON,
                )
                for child in children.ids
            ),
            return_exceptions=True,  # a child that fails is the child's; the parent stays PARSED
        )
        await workflow.execute_activity(
            attachments.finish_attachments,
            inp,
            task_queue=TaskQueue.INGEST.value,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=ATTACHMENT_RETRY,
        )

    async def _ocr(self, inp: StageRef) -> ParseOutcome:
        try:
            return await workflow.execute_activity(
                ocr.ocr_document,
                inp,
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(seconds=OCR_TIMEOUT_SECONDS),
                heartbeat_timeout=timedelta(seconds=OCR_HEARTBEAT_SECONDS),
                retry_policy=OCR_RETRY,
            )
        except ActivityError as exc:
            if isinstance(exc.cause, ApplicationError) and exc.cause.type in parsing.PERMANENT:
                raise  # the version or its file is gone, or it was never scanned
            code = (
                ErrorCode.TIMEOUT
                if isinstance(exc.cause, TimeoutError)
                else ErrorCode.OCR_UNAVAILABLE
            )
            return await workflow.execute_activity(
                ocr.mark_ocr_failed,
                parsing.ParseFailure(ref=inp, code=code),
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )


def _needs_ocr(outcome: ParseOutcome) -> bool:
    """Not parsed, not failed, and pages are waiting for the vision model (E4.8)."""
    return (
        not outcome.parsed
        and outcome.code is None
        and outcome.report is not None
        and bool(outcome.report.ocr_needed_pages)
    )
