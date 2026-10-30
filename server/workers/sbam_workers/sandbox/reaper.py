"""`ReapSandboxVms`: destroy the sandbox VMs nothing owns any more (E13.2, ADR 0023, decision 10).

The executor destroys its VM in a `finally`, and the host kills every VM at its own TTL. This job is
the third line, for a worker that died between creating a VM and finishing with it, and for a host
whose TTL did not fire. A Temporal Schedule starts it every five minutes on the test queue.

A VM is an orphan when all of these hold:
  * it carries our tag (`sbam_run`): the host may be shared, and a VM that is not ours is never
    touched;
  * it carries our environment (`environment`): staging must not reap production's runs on a shared
    host;
  * it is older than its own TTL (`sbam_ttl_s`, which we set at creation) plus the grace period, so
    a run that is merely slow is never killed, only one that has outlived everything that could
    still own it.

A VM with no readable start time is left alone and counted: killing on a guess is worse than a leak
the operator can see. Destroying is idempotent, so two reapers racing do no harm.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sbam_contracts.runs import TaskQueue
    from sbam_contracts.sandbox import VM_TAG_KEY

    from sbam_workers.registry import activity_for, startup_for, workflow_for
    from sbam_workers.sandbox.backend import SandboxBackend, VmInfo
    from sbam_workers.sandbox.factory import make_sandbox
    from sbam_workers.settings import WorkerSettings

REAP_WORKFLOW: Final = "ReapSandboxVms"
REAP_SCHEDULE_ID: Final = "reap-sandbox-vms"
REAP_EVERY: Final = timedelta(minutes=5)
TTL_TAG: Final = "sbam_ttl_s"
ENVIRONMENT_TAG: Final = "environment"
DEFAULT_TTL_S: Final = 3600  # a VM of ours with no readable TTL is judged by this
log = logging.getLogger("sbam_workers.sandbox")


class ReapResult(BaseModel):
    listed: int = 0
    ours: int = 0
    destroyed: int = 0
    failed: int = 0
    unreadable: int = 0  # ours, but with no start time: left alone
    configured: bool = True


def _ttl(vm: VmInfo) -> int:
    try:
        return max(1, int(vm.metadata.get(TTL_TAG, DEFAULT_TTL_S)))
    except (TypeError, ValueError):
        return DEFAULT_TTL_S


def orphans(
    vms: Sequence[VmInfo], now: datetime, grace: timedelta, environment: str
) -> tuple[list[VmInfo], int, int]:
    """(the orphans, how many VMs are ours, how many of ours had no readable start time)."""
    ours = [
        vm
        for vm in vms
        if vm.metadata.get(VM_TAG_KEY) and vm.metadata.get(ENVIRONMENT_TAG) == environment
    ]
    found: list[VmInfo] = []
    unreadable = 0
    for vm in ours:
        if vm.started_at is None:
            unreadable += 1
            continue
        started = vm.started_at if vm.started_at.tzinfo else vm.started_at.replace(tzinfo=UTC)
        if now - started > timedelta(seconds=_ttl(vm)) + grace:
            found.append(vm)
    return found, len(ours), unreadable


async def reap(
    backend: SandboxBackend, now: datetime, grace: timedelta, environment: str
) -> ReapResult:
    vms = await backend.list_vms()
    doomed, ours, unreadable = orphans(vms, now, grace, environment)
    result = ReapResult(listed=len(vms), ours=ours, unreadable=unreadable)
    for vm in doomed:
        try:
            await backend.destroy(vm.vm_id)
        except Exception:  # the next sweep tries again; one stuck VM must not stop the others
            log.error("an orphaned sandbox VM was not destroyed", extra={"vm_id": vm.vm_id})
            result.failed += 1
            continue
        log.warning(
            "an orphaned sandbox VM was destroyed",
            extra={"vm_id": vm.vm_id, "run": vm.metadata.get(VM_TAG_KEY), "alert": "operator"},
        )
        result.destroyed += 1
    return result


@activity_for(TaskQueue.TEST)
@activity.defn(name="reap_sandbox_vms")
async def reap_sandbox_vms(as_of: datetime) -> ReapResult:
    """`as_of` is the workflow clock, so a retry judges the same moment."""
    settings = WorkerSettings()  # type: ignore[call-arg]  # values from the env
    try:
        sandbox = make_sandbox(settings)
    except Exception:  # no sandbox is configured here: nothing to reap, and no reason to fail
        return ReapResult(configured=False)
    return await reap(
        sandbox.backend,
        as_of,
        timedelta(seconds=settings.sandbox_reaper_grace_seconds),
        settings.sbam_env,
    )


@workflow_for(TaskQueue.TEST)
@workflow.defn(name=REAP_WORKFLOW)
class ReapSandboxVms:
    @workflow.run
    async def run(self) -> ReapResult:
        result: ReapResult = await workflow.execute_activity(
            reap_sandbox_vms,
            workflow.now(),
            start_to_close_timeout=timedelta(minutes=4),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        return result


def reap_schedule() -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            REAP_WORKFLOW, id=REAP_SCHEDULE_ID, task_queue=TaskQueue.TEST.value
        ),
        spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=REAP_EVERY)]),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),  # a slow sweep is never doubled
    )


@startup_for(TaskQueue.TEST)
async def ensure_reap_schedule(client: Client) -> None:
    """Create the schedule, or bring an existing one back to this definition (idempotent)."""
    wanted = reap_schedule()
    try:
        await client.create_schedule(REAP_SCHEDULE_ID, wanted)
        log.info("created schedule %s (every %s)", REAP_SCHEDULE_ID, REAP_EVERY)
    except ScheduleAlreadyRunningError:

        def converge(current: ScheduleUpdateInput) -> ScheduleUpdate:
            schedule = current.description.schedule
            schedule.action = wanted.action
            schedule.spec = wanted.spec
            schedule.policy = wanted.policy
            return ScheduleUpdate(schedule=schedule)

        await client.get_schedule_handle(REAP_SCHEDULE_ID).update(converge)
