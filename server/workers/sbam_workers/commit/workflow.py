"""`WorkflowCommit` (#766): `workflow-commit-<workflow id>`, started by POST W/commit.

One own workflow, not a stage of ProjectPipeline. It runs four idempotent activities in order:
recheck (the checks, on the approved hash), freeze, register the STAGING entry on the certified
digest, and start Staging through the runtime port. A crash or a worker restart at any point is
completed by Commit again: every step finds what the earlier run already did. Deterministic:
everything with side effects is an activity.
"""

from datetime import timedelta
from typing import TypeVar

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, TemporalError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.pipeline import IdResult
    from sbam_contracts.runs import MirrorUpdate, RunState, TaskQueue
    from sbam_contracts.workflow_commit import (
        WORKFLOW_COMMIT,
        WorkflowCommitInput,
        WorkflowCommitResult,
    )

    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.registry import workflow_for

QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
STEP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
STEP_TIMEOUT = timedelta(minutes=5)
T = TypeVar("T")


@workflow_for(TaskQueue.CERTIFY)
@workflow.defn(name=WORKFLOW_COMMIT, sandboxed=True)
class WorkflowCommit:
    @workflow.run
    async def run(self, inp: WorkflowCommitInput) -> WorkflowCommitResult:
        self.inp = inp
        stage = "recheck"
        await self._mirror(RunState.RUNNING, stage, None)
        try:
            await self._step("commit_recheck", IdResult)
            stage = "freeze"
            await self._mirror(RunState.RUNNING, stage, None)
            await self._step("commit_freeze", IdResult)
            stage = "register"
            await self._mirror(RunState.RUNNING, stage, None)
            await self._step("commit_register", IdResult)
            stage = "start_staging"
            await self._mirror(RunState.RUNNING, stage, None)
            result = await self._step("commit_start_staging", WorkflowCommitResult)
        except ActivityError as exc:
            reason = getattr(exc.cause, "message", None) or type(exc.cause).__name__
            await self._mirror(RunState.FAILED, stage, f"{stage}: {reason}"[:500])
            raise
        await self._mirror(RunState.COMPLETED, "done", None)
        return result

    async def _step(self, name: str, result_type: type[T]) -> T:
        result: T = await workflow.execute_activity(
            name,
            self.inp,
            task_queue=TaskQueue.CERTIFY.value,
            start_to_close_timeout=STEP_TIMEOUT,
            retry_policy=STEP_RETRY,
            result_type=result_type,
        )
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
