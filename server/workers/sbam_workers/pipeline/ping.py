"""`PingWorkflow`: the sample workflow, registered on all six queues (E1.7-T01).

ping (heartbeating activity on the same queue) -> mirror COMPLETED, or FAILED if ping gave up.
A failed mirror update never fails the run: the reconciler repairs the row later.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.runs import MirrorUpdate, PingResult, RunInput, RunState, TaskQueue

    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.ping import ping
    from sbam_workers.registry import workflow_for

PING_HEARTBEAT_TIMEOUT = timedelta(seconds=10)
PING_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)
MIRROR_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)


@workflow_for(*TaskQueue)
@workflow.defn(name="PingWorkflow")
class PingWorkflow:
    @workflow.run
    async def run(self, inp: RunInput) -> PingResult:
        try:
            result: PingResult = await workflow.execute_activity(
                ping,
                inp,
                start_to_close_timeout=timedelta(minutes=1),
                heartbeat_timeout=PING_HEARTBEAT_TIMEOUT,
                retry_policy=PING_RETRY,
            )
        except ActivityError:
            await self._mirror(inp, RunState.FAILED, "ping", "ping activity failed")
            raise
        await self._mirror(inp, RunState.COMPLETED, "done")
        return result

    async def _mirror(
        self, inp: RunInput, state: RunState, stage: str, error: str | None = None
    ) -> None:
        info = workflow.info()
        update = MirrorUpdate(
            tenant_id=inp.tenant_id,
            project_id=inp.project_id,
            temporal_workflow_id=info.workflow_id,
            temporal_run_id=info.run_id,
            state=state,
            current_stage=stage,
            last_error=error,
        )
        try:
            await workflow.execute_activity(
                update_run_mirror,
                update,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=MIRROR_RETRY,
            )
        except ActivityError:
            workflow.logger.warning("run mirror update failed; the reconciler will repair it")
