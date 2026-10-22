"""`ProjectPipeline` (TD §13.2, E10.1): one Temporal workflow per project, `pipeline-<project_id>`.

    ingest → knowledge → okf → sow → [wait sow_approved] → create_workflow → extract macros →
    extract sub-skills → [wait generate] → build_schema_sql → SubSkillBuild children (≤4 at a time)
    → [wait rebuild_subskills while a Sub-Skill needs review] → certify_macros → validate_workflow →
    certify_workflow → freeze → done

E10.3 (behind `workflow.patched(SCHEDULER_PATCH)`): the build limit and the Sub-Skill dependency
graph come from `plan_builds`, read once per generate; upstream Sub-Skills build first.

Rules this file keeps (.claude/rules/temporal.md):
  * Deterministic: no clock, randomness, env or I/O here. Everything with side effects is an
    activity: stage stubs (owned by their feature issues), `apply_pipeline_transition` (state
    machine + audit + notification, #41) and `notify`.
  * Signals are never authorization: the API authorizes the FDE and the project before it signals.
    FDE events (start_processing, approve_sow, generate, retry, cancel) are applied in the name of
    the signal's `actor_id`; SYSTEM events in the pipeline's.
  * Every transition/notification has an idempotency key "<first run id>:<step>", so activity
    retries, worker crashes and replays never apply anything twice.
  * Human waits have no timeout; after 3 days one reminder goes out. Long waits continue-as-new.
"""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any, NoReturn
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy, SearchAttributeKey
from temporalio.exceptions import ActivityError
from temporalio.workflow import ChildWorkflowCancellationType, ChildWorkflowHandle

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.pipeline import (
        MAX_PARALLEL_BUILDS,
        ORDER,
        PROJECT_PIPELINE,
        BuildPlan,
        BuildPlanInput,
        BuildProgress,
        Cancel,
        CreateWorkflowInput,
        CreateWorkflowResult,
        FreezeResult,
        Generate,
        IdResult,
        IdsResult,
        IngestInput,
        MacroRef,
        NotifyRequest,
        PipelineInput,
        PipelineStage,
        PipelineStatus,
        RebuildSubSkills,
        ResumeState,
        SowApproved,
        SowGenerateInput,
        StageRef,
        SubSkillBuildInput,
        SubSkillBuildResult,
        TransitionOutcome,
        TransitionRequest,
        WorkflowRef,
    )
    from sbam_contracts.runs import MirrorUpdate, RunInput, RunState, TaskQueue, build_workflow_id
    from sbam_contracts.state_machines import Actor, MachineName

    from sbam_workers.build.plan import plan_builds
    from sbam_workers.build.stubs import build_schema_sql
    from sbam_workers.certify.stubs import (
        certify_macros,
        certify_workflow,
        freeze_and_version,
        validate_workflow,
    )
    from sbam_workers.ingest.stubs import ingest_documents
    from sbam_workers.isatvon.stubs import create_workflow, extract_macro_skills, extract_sub_skills
    from sbam_workers.knowledge.build import build_knowledge
    from sbam_workers.knowledge.bundle import build_okf_bundle
    from sbam_workers.mirror import update_run_mirror
    from sbam_workers.pipeline.build_scheduler import BuildScheduler
    from sbam_workers.pipeline.sub_skill_build import SubSkillBuild
    from sbam_workers.pipeline_state import apply_pipeline_transition, notify
    from sbam_workers.registry import workflow_for
    from sbam_workers.sow.generate import generate_sow

SYSTEM = "SYSTEM"
REMINDER_AFTER = timedelta(days=3)  # TD §13.3 (proposed): remind once, never time out
SBAM_STAGE = SearchAttributeKey.for_keyword("SbamStage")
SBAM_WORKFLOW_ID = SearchAttributeKey.for_keyword("SbamWorkflowId")
QUICK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
# TD §13.3: (queue, start-to-close, attempts) per stage activity.
LLM = (TaskQueue.LLM, timedelta(minutes=15), 3)
PLAN: dict[str, tuple[TaskQueue, timedelta, int]] = {
    "ingest_documents": (TaskQueue.INGEST, timedelta(minutes=10), 3),
    "build_knowledge": (TaskQueue.KNOWLEDGE, timedelta(minutes=60), 5),
    "build_okf_bundle": (TaskQueue.KNOWLEDGE, timedelta(minutes=15), 3),
    "generate_sow": LLM,
    "create_workflow": (TaskQueue.LLM, timedelta(minutes=2), 5),
    "extract_macro_skills": LLM,
    "extract_sub_skills": LLM,
    "build_schema_sql": (TaskQueue.BUILD, timedelta(minutes=5), 2),
    "plan_builds": (TaskQueue.BUILD, timedelta(minutes=1), 3),
    "certify_macros": (TaskQueue.CERTIFY, timedelta(minutes=2), 5),
    "validate_workflow": (TaskQueue.CERTIFY, timedelta(minutes=2), 5),
    "certify_workflow": (TaskQueue.CERTIFY, timedelta(minutes=2), 5),
    "freeze_and_version": (TaskQueue.CERTIFY, timedelta(minutes=2), 5),
}
# TD §13.3 for long stages: heartbeat timeout and retry backoff (initial interval, coefficient).
# Behind `workflow.patched(TUNED_PATCH)`, so runs recorded before it replay unchanged.
TUNED_PATCH = "e5-2-knowledge-heartbeat"
TUNED: dict[str, tuple[timedelta, timedelta, float]] = {
    "build_knowledge": (timedelta(minutes=2), timedelta(seconds=30), 2.0),
}
# E10.3: configurable limit, upstream-first scheduling. Older runs keep the plain semaphore.
SCHEDULER_PATCH = "e10-3-build-scheduler"
WAITING_FOR = {
    PipelineStage.WAIT_SOW_APPROVAL: "sow_approved",
    PipelineStage.WAIT_GENERATE: "generate",
    PipelineStage.WAIT_REBUILD: "rebuild_subskills",
}


def _need[T](value: T | None) -> T:
    """Invariant check that survives `python -O` (unlike assert); returns the value."""
    if value is None:
        raise RuntimeError("pipeline invariant broken: value missing")
    return value


class _Stop(Exception):  # noqa: N818 - control flow inside the workflow, never escapes run()
    """Pipeline ends early (failed or cancelled); state is already recorded."""


class _CancelRequested(_Stop):
    """Raised at a checkpoint after a cancel signal; `run` then records the cancellation."""


@workflow_for(TaskQueue.INGEST)
@workflow.defn(name=PROJECT_PIPELINE, sandboxed=True)
class ProjectPipeline:
    def __init__(self) -> None:
        self.stage = PipelineStage.INGEST
        self.waiting_for: str | None = None
        self.sow_approved: SowApproved | None = None
        self.generate: Generate | None = None
        self.rebuilds: list[RebuildSubSkills] = []
        self.cancel_request: Cancel | None = None
        self.seen_keys: set[str] = set()
        self.workflow_id: UUID | None = None
        self.sub_skill_ids: list[UUID] = []
        self.needs_review: list[UUID] = []
        self.done: set[UUID] = set()
        self.blocked: set[UUID] = set()  # needs review only because an upstream isn't certified
        self.build_plan: BuildPlan | None = None
        self.scheduler: BuildScheduler | None = None
        self.builds_started: dict[str, int] = {}
        self.children: dict[str, ChildWorkflowHandle[Any, Any]] = {}
        self.reminders_sent: list[PipelineStage] = []
        self.failed_stage: PipelineStage | None = None
        self.last_error: str | None = None
        self.refs: dict[str, UUID] = {}
        self.ended = False

    # --- signals (never authorization: the API checks the FDE and project first) -----------------

    @workflow.signal(name="sow_approved")
    def on_sow_approved(self, signal: SowApproved) -> None:
        if not self.ended and self.sow_approved is None:  # buffered if early; repeats ignored
            self.sow_approved = signal

    @workflow.signal(name="generate")
    def on_generate(self, signal: Generate) -> None:
        if self._first(signal.idempotency_key) and self.generate is None:
            self.generate = signal

    @workflow.signal(name="rebuild_subskills")
    def on_rebuild(self, signal: RebuildSubSkills) -> None:
        if self._first(signal.idempotency_key):
            self.rebuilds.append(signal)

    @workflow.signal(name="cancel")
    def on_cancel(self, signal: Cancel) -> None:
        if self.ended or self.cancel_request is not None:
            return
        self.cancel_request = signal
        for child in self.children.values():  # cancellation propagates to running builds
            child.cancel()

    def _first(self, key: str) -> bool:
        if self.ended or key in self.seen_keys:
            return False
        self.seen_keys.add(key)
        return True

    # --- queries ---------------------------------------------------------------------------------

    @workflow.query(name="current_stage")
    def current_stage(self) -> str:
        return self.stage.value

    @workflow.query(name="status")
    def status(self) -> PipelineStatus:
        return PipelineStatus(
            stage=self.stage,
            waiting_for=self.waiting_for,
            workflow_id=self.workflow_id,
            sub_skills_total=len(self.sub_skill_ids),
            sub_skills_done=len(self.done),
            failed_stage=self.failed_stage,
            last_error=self.last_error,
            reminders_sent=list(self.reminders_sent),
        )

    @workflow.query(name="build_schedule")
    def build_schedule(self) -> BuildProgress:
        plan = self.build_plan or BuildPlan()
        now = self.scheduler
        if now is None:
            return BuildProgress(
                max_parallel=plan.max_parallel,
                blocked=sorted(self.blocked, key=str),
                certified=len(self.done),
                needs_review=len(self.needs_review),
            )
        in_round = set(now.running) | set(now.needs_review())
        earlier = [s for s in self.needs_review if s not in in_round]  # not rebuilt this round
        return BuildProgress(
            running=list(now.running),
            queued=list(now.queued),
            blocked=list(now.blocked),
            max_parallel=now.limit,
            certified=len(self.done),
            needs_review=len(now.failed) + len(now.blocked) + len(earlier),
        )

    # --- run -------------------------------------------------------------------------------------

    @workflow.run
    async def run(self, inp: PipelineInput) -> PipelineStatus:
        self.inp = inp
        start = 0
        if inp.resume is not None:
            self._restore(inp.resume)
            start = ORDER.index(inp.resume.stage)
        try:
            for stage in ORDER[start:]:
                self._checkpoint()
                await getattr(self, f"_stage_{stage.value}")()
        except _CancelRequested:
            await self._cancelled()
        except _Stop:
            pass
        self.ended = True
        return self.status()

    # --- stages ----------------------------------------------------------------------------------

    async def _stage_ingest(self) -> None:
        await self._enter(PipelineStage.INGEST)
        await self._project("start_processing", Actor.FDE, self.inp.actor_id)
        ingest = IngestInput(**self._base(), document_version_ids=self.inp.document_version_ids)
        await self._attempt(
            ingest_documents, ingest, IdsResult, ("ingestion_failed", MachineName.PROJECT_PIPELINE)
        )
        await self._project("ingestion_completed")

    async def _stage_knowledge(self) -> None:
        await self._enter(PipelineStage.KNOWLEDGE)
        failed = ("knowledge_failed", MachineName.PROJECT_PIPELINE)
        result = await self._attempt(build_knowledge, self._run_input(), IdResult, failed)
        self.refs["knowledge"] = result.id

    async def _stage_okf(self) -> None:
        await self._enter(PipelineStage.OKF)
        ref = StageRef(**self._base(), ref_id=self.refs["knowledge"])
        failed = ("knowledge_failed", MachineName.PROJECT_PIPELINE)
        result = await self._attempt(build_okf_bundle, ref, IdResult, failed)
        self.refs["okf"] = result.id
        await self._project("knowledge_ready")

    async def _stage_sow(self) -> None:
        await self._enter(PipelineStage.SOW)
        ref = SowGenerateInput(**self._base(), ref_id=self.refs["okf"], hint=self.inp.sow_hint)
        failed = ("sow_generation_failed", MachineName.PROJECT_PIPELINE)
        await self._attempt(generate_sow, ref, IdResult, failed)
        await self._project("sow_generated")

    async def _stage_wait_sow_approval(self) -> None:
        await self._wait(PipelineStage.WAIT_SOW_APPROVAL, lambda: self.sow_approved is not None)
        approval = _need(self.sow_approved)
        await self._project("approve_sow", Actor.FDE, approval.actor_id)

    async def _stage_create_workflow(self) -> None:
        await self._enter(PipelineStage.CREATE_WORKFLOW)
        approval = _need(self.sow_approved)
        create = CreateWorkflowInput(
            **self._base(),
            sow_version_id=approval.sow_version_id,
            sha256=approval.sha256,
        )
        result = await self._attempt(create_workflow, create, CreateWorkflowResult, None)
        self.workflow_id = result.workflow_id
        await self._transition(MachineName.WORKFLOW, self.workflow_id, "sow_approved", create=True)
        await self._project("workflow_created")
        if self.inp.search_attributes:
            workflow.upsert_search_attributes([SBAM_WORKFLOW_ID.value_set(str(self.workflow_id))])

    async def _stage_extract_macros(self) -> None:
        await self._enter(PipelineStage.EXTRACT_MACROS)
        await self._workflow("start_extraction")
        failed = ("extraction_failed", MachineName.WORKFLOW)
        result = await self._attempt(extract_macro_skills, self._workflow_ref(), IdsResult, failed)
        self.refs.update({f"macro:{i}": macro for i, macro in enumerate(result.ids)})

    async def _stage_extract_sub_skills(self) -> None:
        await self._enter(PipelineStage.EXTRACT_SUB_SKILLS)
        macros = [v for k, v in sorted(self.refs.items()) if k.startswith("macro:")]
        failed = ("extraction_failed", MachineName.WORKFLOW)
        results = await asyncio.gather(  # parallel per Macro Skill (TD §13.2)
            *(
                self._attempt(
                    extract_sub_skills,
                    MacroRef(**self._base(), workflow_id=_need(self.workflow_id), macro_skill_id=m),
                    IdsResult,
                    failed,
                )
                for m in macros
            )
        )
        self.sub_skill_ids = [sid for r in results for sid in r.ids]
        await self._workflow("extraction_completed")

    async def _stage_wait_generate(self) -> None:
        await self._wait(PipelineStage.WAIT_GENERATE, lambda: self.generate is not None)
        await self._workflow("generate", Actor.FDE, _need(self.generate).actor_id)

    async def _stage_build_schema_sql(self) -> None:
        await self._enter(PipelineStage.BUILD_SCHEMA_SQL)
        await self._attempt(build_schema_sql, self._workflow_ref(), IdResult, None)

    async def _stage_build_sub_skills(self) -> None:
        await self._enter(PipelineStage.BUILD_SUB_SKILLS)
        wanted = set(_need(self.generate).sub_skill_ids)  # empty = every Sub-Skill
        targets = [s for s in self.sub_skill_ids if not wanted or s in wanted]
        if workflow.patched(SCHEDULER_PATCH):
            plan_input = BuildPlanInput(
                **self._base(),
                workflow_id=_need(self.workflow_id),
                sub_skill_ids=self.sub_skill_ids,
            )
            self.build_plan = await self._attempt(plan_builds, plan_input, BuildPlan, None)
        self.needs_review = await self._build(targets)

    async def _stage_wait_rebuild(self) -> None:
        round_no = 0
        while self.needs_review:
            await self._workflow("sub_skill_needs_review", nth=round_no)
            await self._wait(PipelineStage.WAIT_REBUILD, lambda: bool(self.rebuilds))
            rebuild = self.rebuilds.pop(0)
            await self._workflow("retry", Actor.FDE, rebuild.actor_id, nth=round_no)
            await self._enter(PipelineStage.BUILD_SUB_SKILLS)
            again = self._with_unblocked(
                [s for s in self.needs_review if s in set(rebuild.sub_skill_ids)]
            )
            still = await self._build(again)
            self.needs_review = [s for s in self.needs_review if s not in again] + still
            round_no += 1
        await self._workflow("all_sub_skills_certified")

    async def _stage_certify_macros(self) -> None:
        await self._enter(PipelineStage.CERTIFY_MACROS)
        await self._attempt(certify_macros, self._workflow_ref(), IdsResult, None)

    async def _stage_validate_workflow(self) -> None:
        await self._enter(PipelineStage.VALIDATE_WORKFLOW)
        failed = ("workflow_validation_failed", MachineName.WORKFLOW)
        await self._attempt(validate_workflow, self._workflow_ref(), IdResult, failed)

    async def _stage_certify_workflow(self) -> None:
        await self._enter(PipelineStage.CERTIFY_WORKFLOW)
        await self._attempt(certify_workflow, self._workflow_ref(), IdResult, None)
        await self._workflow("workflow_certified")

    async def _stage_freeze(self) -> None:
        await self._enter(PipelineStage.FREEZE)
        await self._attempt(freeze_and_version, self._workflow_ref(), FreezeResult, None)
        await self._workflow("freeze")

    async def _stage_done(self) -> None:
        self.stage = PipelineStage.DONE
        await self._mirror(RunState.COMPLETED)

    # --- building --------------------------------------------------------------------------------

    async def _build(self, sub_skill_ids: list[UUID]) -> list[UUID]:
        """Run SubSkillBuild children, at most MAX_PARALLEL_BUILDS at a time; returns the ones
        that need FDE review."""
        if self.build_plan is not None:
            return await self._schedule(sub_skill_ids, self.build_plan)
        slots = asyncio.Semaphore(MAX_PARALLEL_BUILDS)

        async def one(sub_skill_id: UUID) -> SubSkillBuildResult | None:
            async with slots:
                return await self._build_one(sub_skill_id)

        results = await asyncio.gather(*(one(s) for s in sub_skill_ids), return_exceptions=True)
        self._checkpoint()  # a cancel during the builds stops here
        review = []
        for sub_skill_id, result in zip(sub_skill_ids, results, strict=True):
            if isinstance(result, SubSkillBuildResult) and result.certified:
                self.done.add(sub_skill_id)
            else:
                review.append(sub_skill_id)
        return review

    async def _schedule(self, sub_skill_ids: list[UUID], plan: BuildPlan) -> list[UUID]:
        """E10.3: at most `plan.max_parallel` children at once (counted here, in workflow state),
        each started once its upstreams are certified; returns the ones that need FDE review."""
        scheduler = BuildScheduler(sub_skill_ids, plan.depends_on, plan.max_parallel, self.done)
        self.scheduler = scheduler
        running: dict[UUID, asyncio.Task[SubSkillBuildResult | None]] = {}
        while True:
            if self.cancel_request is None:
                for sub_skill_id in scheduler.start_ready():
                    running[sub_skill_id] = asyncio.create_task(self._build_one(sub_skill_id))
            if not running:
                break
            await workflow.wait_condition(lambda: any(t.done() for t in running.values()))
            for sub_skill_id in sorted((s for s, t in running.items() if t.done()), key=str):
                task = running.pop(sub_skill_id)
                result = None if task.cancelled() or task.exception() else task.result()
                certified = result is not None and result.certified
                scheduler.finished(sub_skill_id, certified)
                if certified:
                    self.done.add(sub_skill_id)
        self.scheduler = None
        self._checkpoint()  # a cancel during the builds stops here
        self.blocked = (self.blocked - set(sub_skill_ids)) | set(scheduler.blocked)
        return scheduler.needs_review()

    def _with_unblocked(self, chosen: list[UUID]) -> list[UUID]:
        """Add blocked Sub-Skills whose upstreams are all certified or rebuilt in this round, so an
        FDE who rebuilds an upstream doesn't have to name each dependent too."""
        if self.build_plan is None:
            return chosen
        depends_on = self.build_plan.depends_on
        picked = set(chosen)
        grew = True
        while grew:
            grew = False
            for s in self.needs_review:
                ups = depends_on.get(s, [])
                if (
                    s in self.blocked
                    and s not in picked
                    and all(u in self.done or u in picked for u in ups)
                ):
                    picked.add(s)
                    grew = True
        return [s for s in self.needs_review if s in picked]

    async def _build_one(self, sub_skill_id: UUID) -> SubSkillBuildResult | None:
        if self.cancel_request is not None:
            return None
        key = str(sub_skill_id)
        build_no = self.builds_started.get(key, 0) + 1
        self.builds_started[key] = build_no
        child = await workflow.start_child_workflow(
            SubSkillBuild.run,
            SubSkillBuildInput(
                **self._base(),
                workflow_id=_need(self.workflow_id),
                sub_skill_id=sub_skill_id,
                build_no=build_no,
            ),
            id=build_workflow_id(sub_skill_id, build_no),
            task_queue=TaskQueue.BUILD.value,
            # Request the cancel and move on: a running activity only hears it on its next
            # (throttled) heartbeat, so waiting for it would stall the pipeline's cancel.
            cancellation_type=ChildWorkflowCancellationType.TRY_CANCEL,
        )
        self.children[key] = child
        if self.cancel_request is not None:  # cancel arrived while this child started
            child.cancel()
        try:
            result: SubSkillBuildResult = await child
            return result
        finally:
            self.children.pop(key, None)

    # --- helpers ---------------------------------------------------------------------------------

    def _base(self) -> dict[str, Any]:
        return {
            "tenant_id": self.inp.tenant_id,
            "project_id": self.inp.project_id,
            "run_id": self.inp.run_id,
            "correlation_id": self.inp.correlation_id,
        }

    def _run_input(self) -> RunInput:
        return RunInput(**self._base())

    def _workflow_ref(self) -> WorkflowRef:
        return WorkflowRef(**self._base(), workflow_id=_need(self.workflow_id))

    def _key(self, step: str) -> str:
        return f"{workflow.info().first_execution_run_id}:{step}"

    async def _enter(self, stage: PipelineStage) -> None:
        self.stage = stage
        await self._mirror(RunState.RUNNING)
        if self.inp.search_attributes:
            workflow.upsert_search_attributes([SBAM_STAGE.value_set(stage.value)])

    async def _attempt(
        self,
        activity: Callable[..., Any],
        arg: Any,
        result_type: type[Any],
        failure: tuple[str, MachineName] | None,
    ) -> Any:
        """Run one stage activity (TD §13.3 timeouts/retries); on final failure record the failure
        transition + a notification and stop the pipeline."""
        name: str = getattr(activity, "__temporal_activity_definition").name
        queue, timeout, attempts = PLAN[name]
        retry = RetryPolicy(maximum_attempts=attempts)
        heartbeat: timedelta | None = None
        if name in TUNED and workflow.patched(TUNED_PATCH):
            heartbeat, initial, coefficient = TUNED[name]
            retry = RetryPolicy(
                initial_interval=initial, backoff_coefficient=coefficient, maximum_attempts=attempts
            )
        try:
            return await workflow.execute_activity(
                name,
                arg,
                task_queue=queue.value,
                start_to_close_timeout=timeout,
                heartbeat_timeout=heartbeat,
                retry_policy=retry,
                result_type=result_type,
            )
        except ActivityError as exc:
            await self._fail(name, exc, failure)

    async def _fail(
        self, step: str, exc: ActivityError, failure: tuple[str, MachineName] | None
    ) -> NoReturn:
        cause = exc.cause
        error_type = getattr(cause, "type", None) or type(cause).__name__
        self.failed_stage = self.stage
        self.last_error = f"{step}: {error_type}"
        if failure is not None:
            event, machine = failure
            entity = self.workflow_id if machine is MachineName.WORKFLOW else self.inp.project_id
            await self._transition(machine, _need(entity), event)
        await self._notify(
            "pipeline.failed",
            {"stage": self.stage.value, "error": error_type, "next_action": "retry"},
        )
        self.stage = PipelineStage.FAILED
        await self._mirror(RunState.FAILED)
        raise _Stop

    async def _cancelled(self) -> None:
        """Cancel: children are already cancelled (signal handler); record CANCELLED on the
        Workflow if SBAM created one, else on the project; notify. Later signals are ignored."""
        request = _need(self.cancel_request)
        self.stage = PipelineStage.CANCELLED
        try:
            if self.workflow_id is not None:
                await self._workflow("cancel", Actor.FDE, request.actor_id)
            else:
                await self._project("cancel", Actor.FDE, request.actor_id)
        except ActivityError:  # e.g. CERTIFIED / FROZEN can't be cancelled; the run still stops
            self.last_error = "cancel transition rejected"
        await self._notify("pipeline.cancelled", {"reason": request.reason})
        await self._mirror(RunState.CANCELLED)

    def _checkpoint(self) -> None:
        if self.cancel_request is not None:
            raise _CancelRequested

    async def _wait(self, stage: PipelineStage, ready: Callable[[], bool]) -> None:
        await self._enter(stage)
        self.waiting_for = WAITING_FOR[stage]

        def done() -> bool:
            return ready() or self.cancel_request is not None

        self._maybe_continue_as_new(stage, done)
        if stage not in self.reminders_sent:
            try:
                await workflow.wait_condition(done, timeout=REMINDER_AFTER)
            except TimeoutError:
                self.reminders_sent.append(stage)
                await self._notify("pipeline.reminder", {"waiting_for": self.waiting_for or ""})
        while not done():
            await workflow.wait_condition(lambda: done() or self._history_too_long())
            self._maybe_continue_as_new(stage, done)
        self.waiting_for = None
        self._checkpoint()

    def _history_too_long(self) -> bool:
        limit = self.inp.continue_as_new_after
        info = workflow.info()
        if limit is not None:
            return info.get_current_history_length() >= limit
        return info.is_continue_as_new_suggested()

    def _maybe_continue_as_new(self, stage: PipelineStage, done: Callable[[], bool]) -> None:
        if done() or not self._history_too_long():
            return
        resume = ResumeState(
            stage=stage,
            workflow_id=self.workflow_id,
            sub_skill_ids=self.sub_skill_ids,
            needs_review=self.needs_review,
            seen_keys=sorted(self.seen_keys),
            reminders_sent=self.reminders_sent,
            sow_approved=self.sow_approved,
            builds_started=self.builds_started,
            certified=sorted(self.done, key=str),
            blocked=sorted(self.blocked, key=str),
            build_plan=self.build_plan,
            refs=dict(self.refs),
        )
        workflow.continue_as_new(self.inp.model_copy(update={"resume": resume}))

    def _restore(self, resume: ResumeState) -> None:
        self.refs.update(resume.refs)  # a regeneration resumes at the SOW stage (E6.3)
        self.workflow_id = resume.workflow_id
        self.sub_skill_ids = list(resume.sub_skill_ids)
        self.needs_review = list(resume.needs_review)
        self.seen_keys = set(resume.seen_keys)
        self.reminders_sent = list(resume.reminders_sent)
        self.sow_approved = self.sow_approved or resume.sow_approved
        self.builds_started = dict(resume.builds_started)
        self.done = set(resume.certified)
        self.blocked = set(resume.blocked)
        self.build_plan = resume.build_plan

    # --- state, notifications, mirror ------------------------------------------------------------

    async def _project(
        self, event: str, actor: Actor = Actor.SYSTEM, actor_id: str = SYSTEM
    ) -> None:
        await self._transition(
            MachineName.PROJECT_PIPELINE, self.inp.project_id, event, actor, actor_id
        )

    async def _workflow(
        self, event: str, actor: Actor = Actor.SYSTEM, actor_id: str = SYSTEM, nth: int = 0
    ) -> None:
        await self._transition(
            MachineName.WORKFLOW, _need(self.workflow_id), event, actor, actor_id, nth=nth
        )

    async def _transition(
        self,
        machine: MachineName,
        entity_id: UUID,
        event: str,
        actor: Actor = Actor.SYSTEM,
        actor_id: str = SYSTEM,
        *,
        create: bool = False,
        nth: int = 0,
    ) -> TransitionOutcome:
        request = TransitionRequest(
            tenant_id=self.inp.tenant_id,
            project_id=self.inp.project_id,
            machine=machine,
            entity_id=entity_id,
            event=event,
            actor=actor,
            actor_id=actor_id,
            correlation_id=self.inp.correlation_id,
            idempotency_key=self._key(f"{machine.value}:{event}:{nth}"),
            create=create,
        )
        outcome: TransitionOutcome = await workflow.execute_activity(
            apply_pipeline_transition,
            request,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=QUICK_RETRY,
        )
        return outcome

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
                idempotency_key=self._key(f"{kind}:{self.stage.value}"),
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=QUICK_RETRY,
        )

    async def _mirror(self, state: RunState) -> None:
        info = workflow.info()
        update = MirrorUpdate(
            tenant_id=self.inp.tenant_id,
            project_id=self.inp.project_id,
            temporal_workflow_id=info.workflow_id,
            temporal_run_id=info.first_execution_run_id,  # the row the API wrote at start
            state=state,
            current_stage=self.stage.value,
            last_error=self.last_error,
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
