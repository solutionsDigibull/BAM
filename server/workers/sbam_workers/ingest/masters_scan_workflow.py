"""`MastersScanImport` (#609, N-02b option (a)): `masters-scan-<scan id>` on sbam-ingest, started by
the masters upload routes for a client that asked to respond asynchronously.

One own workflow, never a stage of ProjectPipeline. `scan_and_import_masters` does the whole job
(`masters_scan.py`). Its retry policy retries only what is not the file's fault (clamd down, the
bulkhead full, storage unavailable); the answer a file earns is stored and the activity returns.
After the last attempt `fail_masters_scan` stores FAILED with `SCAN_UNAVAILABLE`. A DOCUMENT that
ends clean and unparsed then starts `MasterDocumentIngestion` for it, as the synchronous attach
does, without waiting for it. The run mirror shows the stage (`queued`, `scanning`) and the end.
Deterministic: everything with side effects is an activity.
"""

from datetime import timedelta
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, TemporalError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.documents import MASTER_DOCUMENT_INGESTION
    from sbam_contracts.masters_scan import (
        MASTERS_SCAN_IMPORT,
        MastersScanInput,
        MastersScanKind,
        MastersScanResult,
        MastersScanState,
    )
    from sbam_contracts.pipeline import StageRef
    from sbam_contracts.runs import (
        MirrorUpdate,
        RunState,
        TaskQueue,
        master_document_workflow_id,
    )

    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.registry import workflow_for

QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
SCAN_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
    non_retryable_error_types=["NotFound"],
)
SCAN_TIMEOUT = timedelta(minutes=10)  # one attempt: scan + parse of a 15 MB file


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=MASTERS_SCAN_IMPORT, sandboxed=True)
class MastersScanImport:
    @workflow.run
    async def run(self, inp: MastersScanInput) -> MastersScanResult:
        self.inp = inp
        await self._mirror(RunState.RUNNING, "scanning", None)
        try:
            result: MastersScanResult = await workflow.execute_activity(
                "scan_and_import_masters",
                inp,
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=SCAN_TIMEOUT,
                retry_policy=SCAN_RETRY,
                result_type=MastersScanResult,
            )
        except ActivityError:
            result = await workflow.execute_activity(
                "fail_masters_scan",
                inp,
                task_queue=TaskQueue.INGEST.value,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=QUICK_RETRY,
                result_type=MastersScanResult,
            )
        if result.state is MastersScanState.FAILED:
            code = result.code.value if result.code else "FAILED"
            await self._mirror(RunState.FAILED, "failed", code)
            return result
        if inp.kind is MastersScanKind.DOCUMENT and result.parse_pending and result.document_id:
            await self._start_parse(result.document_id)
        await self._mirror(RunState.COMPLETED, "done", None)
        return result

    async def _start_parse(self, document_id: UUID) -> None:
        """Hand the document to `MasterDocumentIngestion` and let it run on its own."""
        ref = StageRef(
            tenant_id=self.inp.tenant_id,
            project_id=self.inp.project_id,
            run_id=workflow.uuid4(),
            correlation_id=self.inp.correlation_id,
            ref_id=document_id,
        )
        try:
            await workflow.start_child_workflow(
                MASTER_DOCUMENT_INGESTION,
                ref,
                id=master_document_workflow_id(document_id),
                task_queue=TaskQueue.INGEST.value,
                parent_close_policy=workflow.ParentClosePolicy.ABANDON,
            )
        except TemporalError:  # already running or done for this document: nothing to start
            workflow.logger.info("the document's parse was already started")

    async def _mirror(self, state: RunState, stage: str, error: str | None) -> None:
        info = workflow.info()
        update = MirrorUpdate(
            tenant_id=self.inp.tenant_id,
            project_id=self.inp.project_id,
            temporal_workflow_id=info.workflow_id,
            temporal_run_id=info.first_execution_run_id,
            state=state,
            current_stage=stage,
            last_error=error,
        )
        try:
            await workflow.execute_activity(
                update_run_mirror,
                update,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=QUICK_RETRY,
            )
        except ActivityError:
            workflow.logger.warning("run mirror update failed; the reconciler will repair it")
