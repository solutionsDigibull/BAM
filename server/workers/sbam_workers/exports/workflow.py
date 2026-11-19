"""`ExportVersion` (#133): `export-<run id>`, started by POST W/versions/{version}/export.

One activity (`export_version`) and the run mirror around it. Deterministic: everything with side
effects is the activity.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, TemporalError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.exports import EXPORT_ACTIVITY, EXPORT_VERSION, ExportInput, ExportResult
    from sbam_contracts.runs import MirrorUpdate, RunState, TaskQueue

    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.registry import workflow_for

QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
EXPORT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=5,
)
EXPORT_TIMEOUT = timedelta(minutes=30)  # 500 Sub-Skills


@workflow_for(TaskQueue.CERTIFY)
@workflow.defn(name=EXPORT_VERSION, sandboxed=True)
class ExportVersion:
    @workflow.run
    async def run(self, inp: ExportInput) -> ExportResult:
        self.inp = inp
        await self._mirror(RunState.RUNNING, "export", None)
        try:
            result: ExportResult = await workflow.execute_activity(
                EXPORT_ACTIVITY,
                inp,
                task_queue=TaskQueue.CERTIFY.value,
                start_to_close_timeout=EXPORT_TIMEOUT,
                retry_policy=EXPORT_RETRY,
                result_type=ExportResult,
            )
        except ActivityError as exc:
            reason = getattr(exc.cause, "message", None) or type(exc.cause).__name__
            await self._mirror(RunState.FAILED, "export", f"export: {reason}"[:500])
            raise
        await self._mirror(RunState.COMPLETED, "done", None)
        return result

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
        except (ActivityError, TemporalError):
            workflow.logger.warning("run mirror update failed; the reconciler will repair it")
