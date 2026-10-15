"""E4.12 `CleanupAbandonedUploads` against Temporal's time-skipping test server (no Docker).

Real workflow, real activity, the real clean-up code on SQLite and a temp staging directory. The
time-skipping server has no Schedule service, so the 03:00 tick is played by the test; that the
Schedule is really created on a real server is checked in tests/integration/test_cleanup_stack.py.
"""

import asyncio
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.ingest.models import UploadReservation
from sbam_contracts.documents import (
    CLEANUP_HOUR_UTC,
    CLEANUP_SCHEDULE_ID,
    CLEANUP_UPLOADS,
    CleanupResult,
)
from sbam_contracts.runs import TaskQueue
from sbam_workers import mirror, registry
from sbam_workers.ingest import cleanup_activities
from sbam_workers.ingest.cleanup_schedule import cleanup_schedule, ensure_cleanup_schedule
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleState,
    WorkflowFailureError,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()


def make_sessions() -> sessionmaker[Session]:
    """Temp-file SQLite like tests/state_db.py: the audit write may use its own connection."""
    fd, path = tempfile.mkstemp(prefix="sbam-cleanup-", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        execution_options={"schema_translate_map": {"sbam": None}},
    )
    for model in (AuditLog, UploadReservation):
        model.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
    return sessionmaker(engine, expire_on_commit=False)


class World:
    def __init__(self, staging: Path) -> None:
        self.sessions = make_sessions()
        self.staging = staging
        staging.mkdir()

    def reservation(self, ends_at: datetime, status: str = "CREATED") -> uuid.UUID:
        with self.sessions() as s:
            row = UploadReservation(
                tenant_id=TENANT,
                project_id=PROJECT,
                batch_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                name="spec.pdf",
                size=10,
                mime="application/pdf",
                status=status,
                expires_at=ends_at,
            )
            s.add(row)
            s.commit()
            rid = row.id
        (self.staging / str(rid)).write_bytes(b"partial")
        (self.staging / f"{rid}.info").write_text("{}")
        return rid

    def staged(self) -> set[str]:
        return {p.name for p in self.staging.iterdir()}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path / "uploads")


@asynccontextmanager
async def environment(world: World) -> AsyncIterator[WorkflowEnvironment]:
    registry.load_all()
    mirror.configure(world.sessions)
    cleanup_activities.configure(world.staging)
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    workflows, activities = registry.collect(TaskQueue.INGEST)
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with (
            env,
            Worker(
                env.client,
                task_queue=TaskQueue.INGEST.value,
                workflows=workflows,
                activities=activities,
                activity_executor=executor,
            ),
        ):
            yield env


async def tick(env: WorkflowEnvironment, run: str) -> CleanupResult:
    """Start the workflow the way the daily schedule does."""
    result: CleanupResult = await env.client.execute_workflow(
        CLEANUP_UPLOADS,
        id=f"{CLEANUP_SCHEDULE_ID}-{run}",
        task_queue=TaskQueue.INGEST.value,
        result_type=CleanupResult,
    )
    return result


def run(world: World, scenario: Callable[[WorkflowEnvironment], Any]) -> Any:
    async def main() -> Any:
        async with environment(world) as env:
            return await scenario(env)

    return asyncio.run(main())


def test_e4_12_the_daily_run_cleans_an_abandoned_upload() -> None:
    world = World(Path(tempfile.mkdtemp(prefix="up")) / "uploads")

    async def scenario(env: WorkflowEnvironment) -> CleanupResult:
        now = await env.get_current_time()
        world.reservation(now - timedelta(hours=1))
        return await tick(env, "1")

    result = run(world, scenario)
    assert result == CleanupResult(files_removed=2) and world.staged() == set()


def test_e4_12_the_clock_is_the_workflows_not_the_activitys() -> None:
    world = World(Path(tempfile.mkdtemp(prefix="up")) / "uploads")

    async def scenario(env: WorkflowEnvironment) -> tuple[CleanupResult, CleanupResult]:
        now = await env.get_current_time()
        world.reservation(now + timedelta(hours=2))  # its hold ends in two hours
        first = await tick(env, "1")
        await env.sleep(timedelta(hours=3))  # the next day's tick comes after the hold ended
        return first, await tick(env, "2")

    first, second = run(world, scenario)
    assert first == CleanupResult() and second.files_removed == 2 and world.staged() == set()


def test_e4_12_a_run_that_could_not_remove_a_file_fails_so_it_is_visible() -> None:
    world = World(Path(tempfile.mkdtemp(prefix="up")) / "uploads")

    async def scenario(env: WorkflowEnvironment) -> None:
        now = await env.get_current_time()
        stuck = world.reservation(now - timedelta(hours=1))
        (world.staging / str(stuck)).unlink()
        (world.staging / str(stuck)).mkdir()  # unlink() refuses a directory: a disk error
        world.reservation(now - timedelta(hours=1))  # this one is still cleaned
        await tick(env, "1")

    with pytest.raises(WorkflowFailureError) as failure:
        run(world, scenario)
    cause = failure.value.cause
    assert isinstance(cause, ApplicationError) and cause.type == "CleanupIncomplete"
    (left,) = world.staging.iterdir()  # only the stuck one remains; the other upload was cleaned
    assert left.is_dir()
    with world.sessions() as s:  # the audit row records the failure
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "UPLOADS_CLEANED")).one()
        assert (entry.after or {})["failed"] == 1


def test_e4_12_without_storage_settings_it_fails_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World(Path(tempfile.mkdtemp(prefix="up")) / "uploads")
    for name in ("STORAGE_ROOT", "S3_BUCKET", "STORAGE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEMPORAL_HOST", "t:7233")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.chdir("/")  # no .env to pick up

    async def scenario(env: WorkflowEnvironment) -> None:
        cleanup_activities.configure(None)  # the real lookup, which finds nothing
        await tick(env, "1")

    with pytest.raises(WorkflowFailureError):
        run(world, scenario)


# --- the schedule ------------------------------------------------------------------------------


class FakeScheduleHandle:
    def __init__(self, schedule: Schedule) -> None:
        self.schedule = schedule
        self.updates = 0

    async def update(self, updater: Callable[[Any], Any]) -> None:
        update = updater(SimpleNamespace(description=SimpleNamespace(schedule=self.schedule)))
        self.schedule = update.schedule
        self.updates += 1


class FakeScheduleClient:
    def __init__(self, existing: Schedule | None = None) -> None:
        self.handle = FakeScheduleHandle(existing) if existing else None
        self.created: list[tuple[str, Schedule]] = []

    async def create_schedule(self, schedule_id: str, schedule: Schedule) -> None:
        if self.handle is not None:
            raise ScheduleAlreadyRunningError()
        self.created.append((schedule_id, schedule))
        self.handle = FakeScheduleHandle(schedule)

    def get_schedule_handle(self, schedule_id: str) -> FakeScheduleHandle:
        assert schedule_id == CLEANUP_SCHEDULE_ID and self.handle is not None
        return self.handle


def test_e4_12_schedule_is_daily_at_0300_utc_on_the_ingest_queue() -> None:
    schedule = cleanup_schedule()
    assert CLEANUP_HOUR_UTC == 3
    (calendar,) = schedule.spec.calendars
    assert list(calendar.hour) == [ScheduleRange(3)]
    assert list(calendar.minute) == [ScheduleRange(0)] and list(calendar.second) == [
        ScheduleRange(0)
    ]
    assert not schedule.spec.cron_expressions and not schedule.spec.time_zone_name  # UTC
    action = schedule.action
    assert isinstance(action, ScheduleActionStartWorkflow)
    assert action.workflow == "CleanupAbandonedUploads" and action.task_queue == "sbam-ingest"
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP


def test_e4_12_it_runs_an_hour_after_the_purge() -> None:
    from sbam_contracts.documents import PURGE_CRON  # "0 2 * * *"

    assert PURGE_CRON.split()[:2] == ["0", "2"] and CLEANUP_HOUR_UTC == 3


def test_e4_12_the_ingest_worker_creates_the_schedule_once() -> None:
    client = FakeScheduleClient()
    asyncio.run(ensure_cleanup_schedule(client))  # type: ignore[arg-type]
    asyncio.run(ensure_cleanup_schedule(client))  # type: ignore[arg-type]  # a second worker starts
    ((schedule_id, _),) = client.created
    assert schedule_id == "cleanup-abandoned-uploads"
    assert client.handle is not None and client.handle.updates == 1  # converged, not duplicated


def test_e4_12_an_existing_schedule_is_brought_up_to_date_and_stays_paused() -> None:
    stale = Schedule(
        action=ScheduleActionStartWorkflow(
            "CleanupAbandonedUploads", id="cleanup-abandoned-uploads", task_queue="sbam-ingest"
        ),
        spec=ScheduleSpec(cron_expressions=["0 5 * * *"]),
        state=ScheduleState(paused=True, note="paused by an operator"),
    )
    client = FakeScheduleClient(stale)
    asyncio.run(ensure_cleanup_schedule(client))  # type: ignore[arg-type]
    assert client.handle is not None
    updated = client.handle.schedule
    assert list(updated.spec.calendars[0].hour) == [ScheduleRange(3)]
    assert updated.state.paused is True and updated.state.note == "paused by an operator"
