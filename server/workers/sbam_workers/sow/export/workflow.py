"""`SowExport` (E6.4): `sow-export-<version>-<format>`, started (or joined) by
GET P/sow/versions/{v}/export.

It only runs `export_sow` (60 s, one retry), so the API can wait for its result and redirect to
the file. Deterministic: the rendering is the activity.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.runs import TaskQueue
    from sbam_contracts.sow_api import SOW_EXPORT, SowExportInput, SowExportResult

    from sbam_workers.registry import workflow_for

EXPORT_TIMEOUT = timedelta(seconds=60)
EXPORT_RETRY = RetryPolicy(maximum_attempts=2, non_retryable_error_types=["ExportNotAllowed"])


@workflow_for(TaskQueue.BUILD)
@workflow.defn(name=SOW_EXPORT, sandboxed=True)
class SowExportWorkflow:
    @workflow.run
    async def run(self, inp: SowExportInput) -> SowExportResult:
        result: SowExportResult = await workflow.execute_activity(
            "export_sow",
            inp,
            task_queue=TaskQueue.BUILD.value,
            start_to_close_timeout=EXPORT_TIMEOUT,
            retry_policy=EXPORT_RETRY,
            result_type=SowExportResult,
        )
        return result
