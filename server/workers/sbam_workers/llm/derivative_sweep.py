"""`SweepLlmDerivatives` (#497): the daily job that deletes cached model answers nobody has used
for `LLM_DERIVATIVE_TTL_DAYS` (default 90).

A Temporal Schedule starts it at 05:00 UTC, after the derived-file sweep (04:00), created by the
LLM worker on start. The workflow is deterministic: it hands `workflow.now()` to the activity,
which does the work (`sbam_api.llm.derivative_sweep`), so a retry judges the same moment.
"""

import logging
from datetime import datetime, timedelta

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sbam_api.llm.derivative_sweep import SweepResult, sweep_llm_derivatives
    from sbam_contracts.runs import TaskQueue

    from sbam_workers import mirror
    from sbam_workers.registry import activity_for, startup_for, workflow_for
    from sbam_workers.settings import WorkerSettings

SWEEP_WORKFLOW = "SweepLlmDerivatives"
SWEEP_SCHEDULE_ID = "sweep-llm-derivatives"
SWEEP_HOUR_UTC = 5
log = logging.getLogger("sbam_workers.llm_derivative_sweep")


@activity_for(TaskQueue.LLM)
@activity.defn(name="sweep_llm_derivatives")
def sweep_derivatives(as_of: datetime) -> SweepResult:
    """`as_of` is the workflow clock, so a retry judges the same moment."""
    settings = WorkerSettings()  # type: ignore[call-arg]  # values from the env
    correlation = f"llm-derivative-sweep-{activity.info().workflow_run_id}"
    with mirror.sessions()() as session:
        return sweep_llm_derivatives(session, as_of, settings.llm_derivative_ttl_days, correlation)


@workflow_for(TaskQueue.LLM)
@workflow.defn(name=SWEEP_WORKFLOW)
class SweepLlmDerivatives:
    @workflow.run
    async def run(self) -> SweepResult:
        result: SweepResult = await workflow.execute_activity(
            sweep_derivatives,
            workflow.now(),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(minutes=5),
                maximum_attempts=3,
            ),
        )
        return result


def sweep_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            SWEEP_WORKFLOW, id=SWEEP_SCHEDULE_ID, task_queue=TaskQueue.LLM.value
        ),
        spec=ScheduleSpec(calendars=[ScheduleCalendarSpec(hour=[ScheduleRange(SWEEP_HOUR_UTC)])]),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP,  # a slow run is never doubled
            catchup_window=timedelta(days=1),
        ),
    )


@startup_for(TaskQueue.LLM)
async def ensure_sweep_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition (idempotent)."""
    wanted = sweep_schedule()
    try:
        await client.create_schedule(SWEEP_SCHEDULE_ID, wanted)
        log.info("created schedule %s (%02d:00 UTC)", SWEEP_SCHEDULE_ID, SWEEP_HOUR_UTC)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(SWEEP_SCHEDULE_ID).update(converge)
