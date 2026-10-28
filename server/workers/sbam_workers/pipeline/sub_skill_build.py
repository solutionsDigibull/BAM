"""`SubSkillBuild` child workflow (TD §13.2, E10.2): build, gate, test and certify one Sub-Skill.

Started by ProjectPipeline with id `build-<sub_skill_id>-<build_no>` on sbam-build. One *cycle* is
a first pass (attempt 0) plus at most MAX_CORRECTIONS correction passes:

    build_manifest → check_libraries → render_skeleton → generate_bodies* → assemble → ast_gate →
    static_gate → generate_tests → build_promptfoo_suite → [tests_started] → run_sandbox_tests →
    certify_sub_skill → [tests_passed: CERTIFIED]            (* first pass of a cycle only: a
                                                              correction supplies the bodies)

A gate or test failure runs `correct` and starts the next pass from the manifest (every gate runs
again; a corrector that changed the ISATVON is picked up by the re-manifest). After
MAX_CORRECTIONS corrections the Sub-Skill goes to NEEDS_FDE_REVIEW and the workflow waits for the
`retry` signal, which opens a new cycle (counter reset) from the manifest. Missing libraries go
DEPENDENCY_FAILED and are corrected the same way. Sandbox infrastructure failures never touch
the counter: TESTING_BLOCKED, then `retry` re-runs only the sandbox tests.

Rules (.claude/rules/temporal.md): deterministic (no clock, randomness, I/O); everything else is an
activity. The counters live in workflow state and survive replay. State changes go through the
`apply_pipeline_transition` activity (state machine + audit + notification, #41) with a key
"<first run id>:sub_skill:<event>:<n>"; the Sub-Skill is assumed to be BUILDING when the child
starts (the `generate` / rebuild that starts the child sets it). `retry` and `cancel` are never
authorization: the API checks the FDE and the project first.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.exceptions import TimeoutError as ActivityTimeout

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.build import (
        BUILD_STATUS_QUERY,
        CANCEL_SIGNAL,
        GATE_FAILED,
        MAX_CORRECTIONS,
        RETRY_SIGNAL,
        TEMPLATE_ERROR,
        TESTS_FAILED,
        BuildStage,
        BuildStageInput,
        CorrectionInput,
        LibraryCheckResult,
        LibraryStatus,
        SubSkillBuildStatus,
        SubSkillRetry,
    )
    from sbam_contracts.pipeline import (
        SUB_SKILL_BUILD,
        Cancel,
        NotifyRequest,
        SubSkillBuildInput,
        SubSkillBuildResult,
        TransitionRequest,
    )
    from sbam_contracts.runs import TaskQueue
    from sbam_contracts.state_machines import Actor, MachineName, SubSkillState

    from sbam_workers.build.manifest import build_manifest
    from sbam_workers.build.stubs import (
        assemble,
        ast_gate,
        check_libraries,
        correct,
        generate_bodies,
        render_skeleton,
        static_gate,
    )
    from sbam_workers.certify.stubs import certify_sub_skill
    from sbam_workers.pipeline_state import apply_pipeline_transition, notify
    from sbam_workers.registry import workflow_for
    from sbam_workers.test.stubs import build_promptfoo_suite, generate_tests, run_sandbox_tests

SYSTEM = "SYSTEM"
DEPENDENCY_MISSING = "DependencyMissing"  # internal failure kind (check_libraries returned MISSING)


@dataclass(frozen=True)
class _Spec:
    """TD §13.3: queue, start-to-close, attempts, non-retryable error types, heartbeat."""

    queue: TaskQueue
    timeout: timedelta
    attempts: int
    non_retryable: tuple[str, ...] = ()
    heartbeat: timedelta | None = None


_MIN = timedelta(minutes=1)
_LLM_ERRORS = ("SchemaInvalidAfterRetries",)
SPECS: dict[BuildStage, _Spec] = {
    BuildStage.BUILD_MANIFEST: _Spec(TaskQueue.BUILD, 2 * _MIN, 3),
    BuildStage.CHECK_LIBRARIES: _Spec(TaskQueue.BUILD, 5 * _MIN, 3),
    BuildStage.RENDER_SKELETON: _Spec(TaskQueue.BUILD, 2 * _MIN, 2, (TEMPLATE_ERROR,)),
    BuildStage.GENERATE_BODIES: _Spec(TaskQueue.LLM, 15 * _MIN, 3, _LLM_ERRORS),
    BuildStage.ASSEMBLE: _Spec(TaskQueue.BUILD, 2 * _MIN, 2, (TEMPLATE_ERROR,)),
    BuildStage.AST_GATE: _Spec(TaskQueue.BUILD, 10 * _MIN, 2, (GATE_FAILED,)),
    BuildStage.STATIC_GATE: _Spec(TaskQueue.BUILD, 10 * _MIN, 2, (GATE_FAILED,)),
    BuildStage.GENERATE_TESTS: _Spec(TaskQueue.LLM, 15 * _MIN, 3, _LLM_ERRORS),
    BuildStage.BUILD_PROMPTFOO_SUITE: _Spec(TaskQueue.TEST, 5 * _MIN, 3),
    # 3 attempts are for sandbox infrastructure only; a test failure is non-retryable.
    BuildStage.RUN_SANDBOX_TESTS: _Spec(
        TaskQueue.TEST, 20 * _MIN, 3, (TESTS_FAILED,), heartbeat=timedelta(seconds=30)
    ),
    BuildStage.CERTIFY: _Spec(TaskQueue.CERTIFY, 2 * _MIN, 5, ("HashMismatch",)),
    BuildStage.CORRECT: _Spec(TaskQueue.LLM, 15 * _MIN, 3, _LLM_ERRORS),
}
STATE_ACTIVITY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=6,
)
# Event -> the state it leads to (SUB_SKILL machine); `retry` depends on where it starts.
TARGET: dict[str, SubSkillState] = {
    "tests_started": SubSkillState.TESTING,
    "tests_passed": SubSkillState.CERTIFIED,
    "tests_failed": SubSkillState.CORRECTING,
    "gate_failed": SubSkillState.CORRECTING,
    "dependency_missing": SubSkillState.DEPENDENCY_FAILED,
    "template_error": SubSkillState.BUILD_ERROR,
    "correction_applied": SubSkillState.BUILDING,
    "attempts_exhausted": SubSkillState.NEEDS_FDE_REVIEW,
    "sandbox_unavailable": SubSkillState.TESTING_BLOCKED,
}
WAITING_STATES = frozenset(
    {
        SubSkillState.NEEDS_FDE_REVIEW,
        SubSkillState.DEPENDENCY_FAILED,
        SubSkillState.TESTING_BLOCKED,
    }
)


class _Failure(Exception):  # noqa: N818 - control flow inside the workflow, never escapes run()
    """A stage failed in a way the correction loop (or the end of the build) handles."""

    def __init__(self, kind: str, gate: BuildStage, finding_ids: list[UUID]) -> None:
        super().__init__(kind)
        self.kind = kind
        self.gate = gate
        self.finding_ids = finding_ids


class _CancelRequested(Exception):  # noqa: N818 - control flow inside the workflow
    """`cancel` signal seen at a checkpoint; `run` ends the build uncertified."""


def _finding_ids(error: ApplicationError) -> list[UUID]:
    """Finding ids a gate persisted before it raised (first detail: a list of id strings)."""
    details = error.details
    if not details or not isinstance(details[0], list):
        return []
    ids: list[UUID] = []
    for raw in details[0]:
        try:
            ids.append(UUID(str(raw)))
        except ValueError:
            continue
    return ids


@workflow_for(TaskQueue.BUILD)
@workflow.defn(name=SUB_SKILL_BUILD)
class SubSkillBuild:
    def __init__(self) -> None:
        self.inp: SubSkillBuildInput | None = None
        self.state = SubSkillState.BUILDING
        self.stage = BuildStage.BUILD_MANIFEST
        self.cycle = 0
        self.attempt = 0
        self.corrections = 0
        self.findings: list[UUID] = []
        self.waiting = False
        self.pending_retry: SubSkillRetry | None = None
        self.retry_keys: list[str] = []
        self.cancel_request: Cancel | None = None
        self.sequence = 0  # makes every transition / notification key unique and replay-stable

    # --- signals (never authorization: the API checks the FDE and project first) -----------------

    @workflow.signal(name=RETRY_SIGNAL)
    def on_retry(self, signal: SubSkillRetry) -> None:
        # Only while waiting for the FDE; a repeated Idempotency-Key is the same request.
        if not self.waiting or signal.idempotency_key in self.retry_keys:
            return
        self.retry_keys.append(signal.idempotency_key)
        self.pending_retry = signal
        self.waiting = False

    @workflow.signal(name=CANCEL_SIGNAL)
    def on_cancel(self, signal: Cancel) -> None:
        if self.cancel_request is None:
            self.cancel_request = signal
            self.waiting = False

    # --- query -----------------------------------------------------------------------------------

    @workflow.query(name=BUILD_STATUS_QUERY)
    def build_status(self) -> SubSkillBuildStatus:
        inp = self._need_input()
        return SubSkillBuildStatus(
            tenant_id=inp.tenant_id,
            project_id=inp.project_id,
            workflow_id=inp.workflow_id,
            sub_skill_id=inp.sub_skill_id,
            build_no=inp.build_no,
            state=self.state,
            stage=self.stage,
            cycle=self.cycle,
            attempt=self.attempt,
            corrections_used=self.corrections,
            waiting_for_retry=self.waiting,
            finding_ids=list(self.findings),
            retry_keys=list(self.retry_keys),
        )

    # --- run -------------------------------------------------------------------------------------

    @workflow.run
    async def run(self, inp: SubSkillBuildInput) -> SubSkillBuildResult:
        self.inp = inp
        try:
            certified = await self._build()
        except _CancelRequested:
            certified = False
        if certified:
            self.stage = BuildStage.DONE
        return SubSkillBuildResult(sub_skill_id=inp.sub_skill_id, certified=certified)

    async def _build(self) -> bool:
        """Cycles until certified (True) or the build ends uncertified (False: BUILD_ERROR)."""
        self._start_cycle(0)
        failure = await self._pass()
        while failure is not None:
            if failure.kind == TEMPLATE_ERROR:  # a platform defect: an admin fixes and retries
                await self._apply("template_error")
                await self._notify("sub_skill.build_error")
                return False
            await self._enter_correction(failure)
            if self.corrections < MAX_CORRECTIONS and await self._correct(failure):
                self.attempt += 1
                failure = await self._pass()
                continue
            await self._exhausted()
            await self._wait_retry()  # the FDE fixed something: a new cycle, counter reset
            self._start_cycle(self.cycle + 1)
            failure = await self._pass()
        return True

    def _start_cycle(self, cycle: int) -> None:
        self.cycle = cycle
        self.attempt = 0
        self.corrections = 0
        self.findings = []

    async def _pass(self) -> _Failure | None:
        """One build pass; None when it certified."""
        try:
            await self._attempt()
        except _Failure as failure:
            self.findings = failure.finding_ids
            return failure
        return None

    async def _attempt(self) -> None:
        ref = self._stage_input()
        await self._call(BuildStage.BUILD_MANIFEST, build_manifest, ref)
        libraries: LibraryCheckResult = await self._call(
            BuildStage.CHECK_LIBRARIES, check_libraries, ref
        )
        if libraries.status is LibraryStatus.MISSING:
            raise _Failure(DEPENDENCY_MISSING, BuildStage.CHECK_LIBRARIES, libraries.finding_ids)
        await self._call(BuildStage.RENDER_SKELETON, render_skeleton, ref)
        if self.attempt == 0:  # after a correction, the corrector supplied the bodies
            await self._call(BuildStage.GENERATE_BODIES, generate_bodies, ref)
        await self._call(BuildStage.ASSEMBLE, assemble, ref)
        await self._call(BuildStage.AST_GATE, ast_gate, ref)
        await self._call(BuildStage.STATIC_GATE, static_gate, ref)
        await self._call(BuildStage.GENERATE_TESTS, generate_tests, ref)
        await self._call(BuildStage.BUILD_PROMPTFOO_SUITE, build_promptfoo_suite, ref)
        await self._apply("tests_started")
        await self._sandbox_tests(ref)
        await self._call(BuildStage.CERTIFY, certify_sub_skill, ref)
        await self._apply("tests_passed")
        await self._notify("sub_skill.certified")

    async def _sandbox_tests(self, ref: BuildStageInput) -> None:
        """Sandbox infrastructure trouble (after Temporal's retries) blocks the Sub-Skill and waits
        for the FDE; it never reaches the correction loop and never uses a correction."""
        while True:
            try:
                await self._call(BuildStage.RUN_SANDBOX_TESTS, run_sandbox_tests, ref)
                return
            except ActivityError as error:
                if not isinstance(error.cause, ApplicationError | ActivityTimeout):
                    raise  # a cancellation, not an infrastructure failure
            await self._apply("sandbox_unavailable")
            await self._notify("sub_skill.testing_blocked")
            await self._wait_retry()  # `retry` takes it back to TESTING

    async def _enter_correction(self, failure: _Failure) -> None:
        """CORRECTING (gates, tests) or DEPENDENCY_FAILED (libraries)."""
        if failure.kind == DEPENDENCY_MISSING:
            await self._apply("dependency_missing")
        elif failure.kind == TESTS_FAILED:
            await self._apply("tests_failed")
        else:
            await self._apply("gate_failed")

    async def _correct(self, failure: _Failure) -> bool:
        """One correction. It is spent even if the corrector changed nothing. False when the
        corrector itself gave up (no further correction would help either)."""
        self.corrections += 1
        try:
            await self._call(
                BuildStage.CORRECT,
                correct,
                CorrectionInput(
                    **self._ref(),
                    cycle=self.cycle,
                    attempt=self.attempt,  # the failed pass: one correction per failed pass
                    failed_gate=failure.gate.value,
                    finding_ids=list(failure.finding_ids),
                ),
            )
        except ActivityError as error:
            if not isinstance(error.cause, ApplicationError):
                raise
            self.corrections = MAX_CORRECTIONS
            return False
        await self._apply("correction_applied")
        return True

    async def _exhausted(self) -> None:
        """No correction left: NEEDS_FDE_REVIEW (DEPENDENCY_FAILED stays as it is), then tell the
        FDE which findings to look at and that `retry` is the next action."""
        if self.state is SubSkillState.CORRECTING:
            await self._apply("attempts_exhausted")
        kind = (
            "sub_skill.dependency_failed"
            if self.state is SubSkillState.DEPENDENCY_FAILED
            else "sub_skill.needs_fde_review"
        )
        await self._notify(kind)

    async def _wait_retry(self) -> None:
        """Wait for `retry` (or `cancel`), then apply it in the FDE's name."""
        self.stage = BuildStage.WAIT_RETRY
        self.pending_retry = None
        self.waiting = True
        await workflow.wait_condition(
            lambda: self.pending_retry is not None or self.cancel_request is not None
        )
        self._checkpoint()
        retry = self.pending_retry
        if retry is None:  # unreachable: the condition above holds only with one of the two
            raise _CancelRequested
        self.pending_retry = None
        await self._apply("retry", Actor.FDE, retry.actor_id)

    # --- helpers ---------------------------------------------------------------------------------

    def _need_input(self) -> SubSkillBuildInput:
        if self.inp is None:
            raise RuntimeError("build not started")
        return self.inp

    def _ref(self) -> dict[str, Any]:
        inp = self._need_input()
        return {
            "tenant_id": inp.tenant_id,
            "project_id": inp.project_id,
            "run_id": inp.run_id,
            "correlation_id": inp.correlation_id,
            "workflow_id": inp.workflow_id,
            "sub_skill_id": inp.sub_skill_id,
            "build_no": inp.build_no,
        }

    def _stage_input(self) -> BuildStageInput:
        return BuildStageInput(**self._ref(), cycle=self.cycle, attempt=self.attempt)

    def _checkpoint(self) -> None:
        if self.cancel_request is not None:
            raise _CancelRequested

    def _key(self, step: str) -> str:
        self.sequence += 1
        return f"{workflow.info().first_execution_run_id}:sub_skill:{step}:{self.sequence}"

    async def _call(self, stage: BuildStage, fn: Callable[..., Any], payload: Any) -> Any:
        """Run a stage activity per TD §13.3. GateFailed / TestsFailed / TemplateError become a
        `_Failure` (finding ids from the error); anything else propagates."""
        self._checkpoint()
        self.stage = stage
        spec = SPECS[stage]
        try:
            return await workflow.execute_activity(
                fn,
                payload,
                task_queue=spec.queue.value,
                start_to_close_timeout=spec.timeout,
                heartbeat_timeout=spec.heartbeat,
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=spec.attempts,
                    non_retryable_error_types=list(spec.non_retryable),
                ),
            )
        except ActivityError as error:
            cause = error.cause
            if isinstance(cause, ApplicationError) and cause.type in (
                GATE_FAILED,
                TESTS_FAILED,
                TEMPLATE_ERROR,
            ):
                raise _Failure(cause.type, stage, _finding_ids(cause)) from None
            raise

    async def _apply(self, event: str, actor: Actor = Actor.SYSTEM, actor_id: str = SYSTEM) -> None:
        inp = self._need_input()
        await workflow.execute_activity(
            apply_pipeline_transition,
            TransitionRequest(
                tenant_id=inp.tenant_id,
                project_id=inp.project_id,
                machine=MachineName.SUB_SKILL,
                entity_id=inp.sub_skill_id,
                event=event,
                actor=actor,
                actor_id=actor_id,
                correlation_id=inp.correlation_id,
                idempotency_key=self._key(event),
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=STATE_ACTIVITY,
        )
        if event == "retry":
            self.state = (
                SubSkillState.TESTING
                if self.state is SubSkillState.TESTING_BLOCKED
                else SubSkillState.BUILDING
            )
        else:
            self.state = TARGET[event]

    async def _notify(self, kind: str) -> None:
        inp = self._need_input()
        payload = {
            "workflow_id": str(inp.workflow_id),
            "build_no": str(inp.build_no),
            "state": self.state.value,
            "cycle": str(self.cycle),
            "corrections_used": str(self.corrections),
        }
        if self.findings:
            payload["finding_ids"] = ",".join(str(f) for f in self.findings)
        if self.state in WAITING_STATES:
            payload["next_action"] = "retry"
        await workflow.execute_activity(
            notify,
            NotifyRequest(
                tenant_id=inp.tenant_id,
                project_id=inp.project_id,
                type=kind,
                entity_type="sub_skill",
                entity_id=inp.sub_skill_id,
                payload=payload,
                correlation_id=inp.correlation_id,
                idempotency_key=self._key(kind),
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=STATE_ACTIVITY,
        )
