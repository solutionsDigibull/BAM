"""`KnowledgeRebuild` (E5.2): `knowledge-<project_id>`, started by POST P/knowledge/rebuild.

build_knowledge -> build_okf_bundle for a new knowledge version, with the pipeline stage's
timeouts and retries. It never moves the project's pipeline state: the version itself records
READY or FAILED (the activities do that), and the FDE gets a notification either way. Deterministic:
everything with side effects is an activity.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.knowledge import KNOWLEDGE_REBUILD
    from sbam_contracts.pipeline import IdResult, NotifyRequest, StageRef
    from sbam_contracts.runs import MirrorUpdate, RunInput, RunState, TaskQueue

    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.pipeline_state import notify
    from sbam_workers.registry import workflow_for

QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
# TD §13.3: build_knowledge 5x, 30 s backoff x2, 60 min start-to-close, heartbeat 2 min.
BUILD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=30), backoff_coefficient=2.0, maximum_attempts=5
)
OKF_RETRY = RetryPolicy(maximum_attempts=3)


@workflow_for(TaskQueue.KNOWLEDGE)
@workflow.defn(name=KNOWLEDGE_REBUILD, sandboxed=True)
class KnowledgeRebuild:
    @workflow.run
    async def run(self, inp: RunInput) -> IdResult:
        self.inp = inp
        await self._mirror(RunState.RUNNING, "knowledge", None)
        try:
            built: IdResult = await workflow.execute_activity(
                "build_knowledge",
                inp,
                task_queue=TaskQueue.KNOWLEDGE.value,
                start_to_close_timeout=timedelta(minutes=60),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=BUILD_RETRY,
                result_type=IdResult,
            )
            await workflow.execute_activity(
                "build_okf_bundle",
                StageRef(**inp.model_dump(), ref_id=built.id),
                task_queue=TaskQueue.KNOWLEDGE.value,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=OKF_RETRY,
                result_type=IdResult,
            )
        except ActivityError as exc:
            error = getattr(exc.cause, "type", None) or type(exc.cause).__name__
            await self._notify(
                "knowledge.rebuild_failed", {"error": error, "next_action": "rebuild"}
            )
            await self._mirror(RunState.FAILED, "knowledge", f"build_knowledge: {error}")
            raise
        await self._notify("knowledge.rebuilt", {"knowledge_version_id": str(built.id)})
        await self._mirror(RunState.COMPLETED, "done", None)
        return built

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
