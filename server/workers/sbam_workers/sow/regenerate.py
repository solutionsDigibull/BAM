"""`SowRegenerate` (E6.3): `sow-<project_id>`, started by POST P/sow/regenerate while the SOW is in
review.

`generate_sow` with the FDE's hint and the pipeline stage's timeouts and retries. It never moves the
project's pipeline state: ProjectPipeline is still waiting for the approval, and the new version
becomes the current one (or, if generation fails, a GENERATION_FAILED version is recorded and the
current one stays). The FDE gets a notification either way. Deterministic: everything with side
effects is an activity.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.pipeline import IdResult, NotifyRequest, SowGenerateInput
    from sbam_contracts.runs import MirrorUpdate, RunState, TaskQueue
    from sbam_contracts.sow_api import SOW_REGENERATE

    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.pipeline_state import notify
    from sbam_workers.registry import workflow_for
    from sbam_workers.sow.generate import GENERATE_SOW_ATTEMPTS

QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
# TD §13.3: the LLM stages, 15 min start-to-close, 3 attempts (as ProjectPipeline's PLAN).
GENERATE_RETRY = RetryPolicy(maximum_attempts=GENERATE_SOW_ATTEMPTS)


@workflow_for(TaskQueue.LLM)
@workflow.defn(name=SOW_REGENERATE, sandboxed=True)
class SowRegenerate:
    @workflow.run
    async def run(self, inp: SowGenerateInput) -> IdResult:
        self.inp = inp
        await self._mirror(RunState.RUNNING, "sow", None)
        try:
            version: IdResult = await workflow.execute_activity(
                "generate_sow",
                inp,
                task_queue=TaskQueue.LLM.value,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=GENERATE_RETRY,
                result_type=IdResult,
            )
        except ActivityError as exc:
            error = getattr(exc.cause, "type", None) or type(exc.cause).__name__
            await self._notify(
                "sow.regeneration_failed", {"error": error, "next_action": "regenerate"}
            )
            await self._mirror(RunState.FAILED, "sow", f"generate_sow: {error}")
            raise
        await self._notify("sow.regenerated", {"sow_version_id": str(version.id)})
        await self._mirror(RunState.COMPLETED, "done", None)
        return version

    async def _notify(self, kind: str, payload: dict[str, str]) -> None:
        await workflow.execute_activity(
            notify,
            NotifyRequest(
                tenant_id=self.inp.tenant_id,
                project_id=self.inp.project_id,
                type=kind,
                entity_type="project",
                entity_id=self.inp.project_id,
                payload=payload,
                correlation_id=self.inp.correlation_id,
                idempotency_key=f"{workflow.info().first_execution_run_id}:{kind}",
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=QUICK_RETRY,
        )

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
