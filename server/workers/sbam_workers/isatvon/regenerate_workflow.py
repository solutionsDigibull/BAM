"""`IsatvonRegenerate` (E8.4): `isatvon-regen-<id>`, started by POST W/macro-skills/{id}/regenerate
and POST W/sub-skills/{id}/regenerate while the Workflow is in ISATVON_REVIEW (or
EXTRACTION_FAILED).

`regenerate_skills` with the pipeline's LLM stage timeouts and retries. It never moves the Workflow
or the project's pipeline state; the FDE gets a notification either way. Deterministic: everything
with side effects is an activity.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.isatvon_workbench import ISATVON_REGENERATE, RegenerateInput
    from sbam_contracts.pipeline import IdsResult, NotifyRequest
    from sbam_contracts.runs import MirrorUpdate, RunState, TaskQueue

    from sbam_workers.isatvon.sub_skills import EXTRACT_ATTEMPTS
    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.pipeline_state import notify
    from sbam_workers.registry import workflow_for

QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
# TD §13.3: the LLM stages, 15 min start-to-close, 3 attempts (as ProjectPipeline's PLAN).
REGENERATE_RETRY = RetryPolicy(maximum_attempts=EXTRACT_ATTEMPTS)


@workflow_for(TaskQueue.LLM)
@workflow.defn(name=ISATVON_REGENERATE, sandboxed=True)
class IsatvonRegenerate:
    @workflow.run
    async def run(self, inp: RegenerateInput) -> IdsResult:
        self.inp = inp
        await self._mirror(RunState.RUNNING, "isatvon", None)
        try:
            result: IdsResult = await workflow.execute_activity(
                "regenerate_skills",
                inp,
                task_queue=TaskQueue.LLM.value,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=REGENERATE_RETRY,
                result_type=IdsResult,
            )
        except ActivityError as exc:
            error = getattr(exc.cause, "type", None) or type(exc.cause).__name__
            await self._notify("isatvon.regeneration_failed", {"error": error})
            await self._mirror(RunState.FAILED, "isatvon", f"regenerate_skills: {error}")
            raise
        await self._notify("isatvon.regenerated", {"sub_skills": str(len(result.ids))})
        await self._mirror(RunState.COMPLETED, "done", None)
        return result

    async def _notify(self, kind: str, payload: dict[str, str]) -> None:
        await workflow.execute_activity(
            notify,
            NotifyRequest(
                tenant_id=self.inp.tenant_id,
                project_id=self.inp.project_id,
                type=kind,
                entity_type="macro_skill",
                entity_id=self.inp.macro_skill_id,
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
