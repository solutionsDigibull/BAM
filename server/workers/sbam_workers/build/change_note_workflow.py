"""`RequestChangeApply` (#767): `request-change-<workflow id>`, started by POST W/request-change.

One own workflow, not a stage of ProjectPipeline: `apply_change_note` classifies the FDE's note;
when the code is to change (REBUILDING) the workflow sends the waiting `SubSkillBuild` the `retry`
it waits for (the same signal the API's retry route sends), and that build's `generate_bodies`
reads the note as data. A held patch (HELD_PATCH) waits for the FDE's decision, which sends the
retry itself; a PARKED note changes nothing. The FDE is notified either way. Deterministic:
everything with side effects is an activity or a signal.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, TemporalError

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.build import RETRY_SIGNAL, SubSkillRetry
    from sbam_contracts.pipeline import NotifyRequest
    from sbam_contracts.request_change import (
        REQUEST_CHANGE_APPLY,
        ChangeNoteStatus,
        RequestChangeInput,
        RequestChangeResult,
    )
    from sbam_contracts.runs import MirrorUpdate, RunState, TaskQueue

    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.pipeline_state import notify
    from sbam_workers.registry import workflow_for

QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
APPLY_RETRY = RetryPolicy(maximum_attempts=3)  # TD §13.3: the LLM stages
NOTIFY_TYPES = {
    ChangeNoteStatus.REBUILDING: "request_change.rebuilding",
    ChangeNoteStatus.HELD_PATCH: "request_change.held_patch",
    ChangeNoteStatus.PARKED: "request_change.parked",
    ChangeNoteStatus.QUEUED: "request_change.queued",
}


@workflow_for(TaskQueue.LLM)
@workflow.defn(name=REQUEST_CHANGE_APPLY, sandboxed=True)
class RequestChangeApply:
    @workflow.run
    async def run(self, inp: RequestChangeInput) -> RequestChangeResult:
        self.inp = inp
        await self._mirror(RunState.RUNNING, "request_change", None)
        try:
            result: RequestChangeResult = await workflow.execute_activity(
                "apply_change_note",
                inp,
                task_queue=TaskQueue.LLM.value,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=APPLY_RETRY,
                result_type=RequestChangeResult,
            )
        except ActivityError as exc:
            error = getattr(exc.cause, "type", None) or type(exc.cause).__name__
            await self._notify("request_change.failed", {"error": error})
            await self._mirror(RunState.FAILED, "request_change", f"apply_change_note: {error}")
            raise
        if result.status is ChangeNoteStatus.REBUILDING:
            await self._retry_build()
        await self._notify(NOTIFY_TYPES[result.status], {"note_id": str(result.note_id)})
        await self._mirror(RunState.COMPLETED, "done", None)
        return result

    async def _retry_build(self) -> None:
        """The waiting build takes the same `retry` the API sends; a repeat key is dropped."""
        handle = workflow.get_external_workflow_handle(self.inp.build_workflow_id)
        try:
            await handle.signal(
                RETRY_SIGNAL,
                SubSkillRetry(
                    reason="Request change: a new generated version from the FDE's note",
                    idempotency_key=f"request-change-{self.inp.note_id}",
                    actor_id=self.inp.actor_id,
                ),
            )
        except TemporalError:  # the build finished or moved on since the note was classified
            workflow.logger.warning("the build did not take the retry")
            await self._notify("request_change.build_not_waiting", {})

    async def _notify(self, kind: str, payload: dict[str, str]) -> None:
        await workflow.execute_activity(
            notify,
            NotifyRequest(
                tenant_id=self.inp.tenant_id,
                project_id=self.inp.project_id,
                type=kind,
                entity_type="sub_skill",
                entity_id=self.inp.sub_skill_id,
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
