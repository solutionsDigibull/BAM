"""E4.9 `PurgeDeleted` against Temporal's time-skipping test server (no Docker).

Real workflow, real activities, the real purge code on SQLite and a local-disk storage root; only
the storage failure of T09 and the derivative / Cognee cleanup (no tables yet) are fakes.

The time-skipping server has no Schedule service (`CreateSchedule` is unimplemented), so the daily
02:00 tick is played by the test: skip the time, then start the workflow the way the schedule would.
That the Schedule is really created with the right cron is checked against a fake client here and
against the real Temporal in tests/integration/test_purge_schedule_stack.py (needs the stack).
"""

import asyncio
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.core.auth import CurrentUser
from sbam_api.ingest import retention
from sbam_api.ingest.models import Document, DocumentVersion, UploadReservation
from sbam_api.projects.models import Project
from sbam_api.storage.base import Kind, StorageBackend, StorageScope, StorageUnavailable
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.documents import (
    PURGE_CRON,
    PURGE_DELETED,
    PURGE_RETENTION_DAYS,
    PURGE_SCHEDULE_ID,
    PurgeResult,
)
from sbam_contracts.runs import TaskQueue
from sbam_workers import mirror, registry
from sbam_workers.ingest import purge_activities
from sbam_workers.ingest.purge_schedule import ensure_purge_schedule, purge_schedule
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

TENANT = uuid.uuid4()
PROJECT = uuid.uuid4()
FDE = uuid.uuid4()
USER = CurrentUser(
    user_id=FDE,
    keycloak_sub="sub-fde",
    tenant_id=TENANT,
    roles=frozenset({"sbam-fde"}),
    assigned_project_ids=frozenset({PROJECT}),
)
DAY = timedelta(days=1)
PDF = b"%PDF-1.7 a small test document\n" * 40
PARSED = b"parsed text of the document\n" * 10


def make_sessions() -> sessionmaker[Session]:
    """Temp-file SQLite like tests/state_db.py: the audit write may use its own connection."""
    fd, path = tempfile.mkstemp(prefix="sbam-purge-", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        execution_options={"schema_translate_map": {"sbam": None}},
    )
    for model in (
        AuditLog,
        NotificationOutbox,
        Project,
        Document,
        DocumentVersion,
        UploadReservation,
    ):
        model.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
    return sessionmaker(engine, expire_on_commit=False)


class FakeCleanup:
    """Derivatives and Cognee references: one derived file per document, and a log of removals."""

    def __init__(self) -> None:
        self.derived: dict[uuid.UUID, list[str]] = {}
        self.removed_references: list[uuid.UUID] = []

    def derived_keys(self, session: Session, ctx: Any, document_id: uuid.UUID) -> Sequence[str]:
        return self.derived.get(document_id, [])

    def remove_references(self, session: Session, ctx: Any, document_id: uuid.UUID) -> None:
        self.removed_references.append(document_id)


class FlakyStorage:
    """A storage that fails after `fail_after` successful deletes, for `failures` times in all."""

    def __init__(self, inner: StorageBackend, fail_after: int, failures: int) -> None:
        self.inner = inner
        self.fail_after = fail_after
        self.failures = failures
        self.deleted: list[str] = []
        self.attempts = 0

    def delete(self, key: str) -> None:
        self.attempts += 1
        if self.failures > 0 and len(self.deleted) >= self.fail_after:
            self.failures -= 1
            raise StorageUnavailable
        self.inner.delete(key)
        self.deleted.append(key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@dataclass
class World:
    sessions: sessionmaker[Session]
    storage: LocalDiskBackend
    cleanup: FakeCleanup
    root: Path
    docs: list[uuid.UUID] = field(default_factory=list)

    def add_document(self, title: str = "spec.pdf", data: bytes = PDF) -> uuid.UUID:
        """An uploaded document with its file and one derived file, like a parsed upload."""
        stored = self.storage.put(
            data, StorageScope(TENANT, PROJECT), Kind.UPLOADS, "application/pdf"
        )
        derived = self.storage.put(
            PARSED + data + title.encode(),
            StorageScope(TENANT, PROJECT),
            Kind.DERIVED,
            "text/plain",
        )
        with self.sessions() as s:
            if s.get(Project, PROJECT) is None:
                s.add(Project(id=PROJECT, tenant_id=TENANT, name="Quote Bot", state="IDLE"))
            document = Document(tenant_id=TENANT, project_id=PROJECT, title=title)
            s.add(document)
            s.flush()
            s.add(
                DocumentVersion(
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    document_id=document.id,
                    storage_key=stored.storage_key,
                    sha256=stored.sha256,
                    size=stored.size,
                    mime="application/pdf",
                    state="PARSED",
                )
            )
            s.commit()
            self.cleanup.derived[document.id] = [derived.storage_key]
            self.docs.append(document.id)
            return document.id

    def delete(self, document_id: uuid.UUID, now: datetime | None = None) -> None:
        with self.sessions() as s:
            retention.delete_document(
                s, USER, PROJECT, document_id, retention.NoUsage(), now=now or datetime.now(UTC)
            )

    def keys(self, document_id: uuid.UUID) -> list[str]:
        with self.sessions() as s:
            version = s.scalars(
                select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            ).one()
            return [version.storage_key, *self.cleanup.derived[document_id]]

    def state(self, document_id: uuid.UUID) -> str:
        with self.sessions() as s:
            return str(
                s.scalars(
                    select(DocumentVersion.state).where(DocumentVersion.document_id == document_id)
                ).one()
            )

    def audit(self, document_id: uuid.UUID) -> list[str]:
        with self.sessions() as s:
            version = s.scalars(
                select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
            ).one()
            ids = {str(document_id), str(version)}
            rows = s.scalars(select(AuditLog).where(AuditLog.entity_id.in_(ids)))
            return [r.action for r in rows]

    def exists(self, key: str) -> bool:
        return self.storage.exists(key)


@asynccontextmanager
async def environment(
    world: World, storage: StorageBackend | None = None
) -> AsyncIterator[WorkflowEnvironment]:
    registry.load_all()
    mirror.configure(world.sessions)
    purge_activities.configure(storage=storage or world.storage, cleanup=world.cleanup)
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    workflows, activities = registry.collect(TaskQueue.INGEST)
    with ThreadPoolExecutor(max_workers=8) as executor:
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


async def tick(env: WorkflowEnvironment, *, after: timedelta | None = None) -> PurgeResult:
    """Skip `after` of time, then start the workflow the way the daily schedule does."""
    if after is not None:
        await env.sleep(after)
    result: PurgeResult = await env.client.execute_workflow(
        PURGE_DELETED,
        id=f"{PURGE_SCHEDULE_ID}-{uuid.uuid4()}",  # Temporal appends the start time like this
        task_queue=TaskQueue.INGEST.value,
        result_type=PurgeResult,
    )
    return result


def run(scenario: Callable[[World], Coroutine[Any, Any, Any]], tmp_path: Path) -> Any:
    world = World(make_sessions(), LocalDiskBackend(tmp_path), FakeCleanup(), tmp_path)
    return asyncio.run(scenario(world))


# --- T05: 90 days later the files are gone -------------------------------------------------------


def test_e4_9_t05_purge_runs_after_90_days_and_removes_files_and_derivatives(
    tmp_path: Path,
) -> None:
    async def scenario(world: World) -> None:
        document = world.add_document()
        keys = world.keys(document)
        world.delete(document)
        assert all(world.exists(k) for k in keys)

        async with environment(world) as env:
            result = await tick(env, after=timedelta(days=PURGE_RETENTION_DAYS) + DAY)

        assert (result.purged, result.skipped, result.failed) == (1, 0, 0)
        assert not any(world.exists(k) for k in keys)  # the file and its derivative
        assert world.state(document) == "PURGED"
        assert world.cleanup.removed_references == [document]  # Cognee references
        with world.sessions() as s:
            row = s.get(Document, document)
            assert row is not None and row.title == "spec.pdf"  # DB tombstone stays
        audit = world.audit(document)
        assert audit.count("DOCUMENT_PURGED") == 1 and audit.count("purge") == 1

    run(scenario, tmp_path)


def test_e4_9_t05_a_second_tick_finds_nothing_left(tmp_path: Path) -> None:
    async def scenario(world: World) -> None:
        document = world.add_document()
        world.delete(document)
        async with environment(world) as env:
            first = await tick(env, after=timedelta(days=PURGE_RETENTION_DAYS) + DAY)
            second = await tick(env, after=DAY)
        assert first.purged == 1 and (second.purged, second.skipped, second.failed) == (0, 0, 0)
        assert world.audit(document).count("DOCUMENT_PURGED") == 1

    run(scenario, tmp_path)


def test_e4_9_the_job_purges_every_tenant_and_leaves_live_documents(tmp_path: Path) -> None:
    async def scenario(world: World) -> None:
        old = world.add_document("old.pdf", PDF)
        live = world.add_document("live.pdf", PDF + b"live")
        recent = world.add_document("recent.pdf", PDF + b"recent")
        world.delete(old, now=datetime.now(UTC) - timedelta(days=100))
        world.delete(recent)
        async with environment(world) as env:
            result = await tick(env)
        assert result.purged == 1
        assert world.state(old) == "PURGED"
        assert world.state(live) == "PARSED" and all(world.exists(k) for k in world.keys(live))
        assert world.state(recent) == "DELETED" and all(world.exists(k) for k in world.keys(recent))

    run(scenario, tmp_path)


# --- T06: not before 90 days ---------------------------------------------------------------------


def test_e4_9_t06_nothing_is_purged_at_89_days_and_the_document_can_still_be_restored(
    tmp_path: Path,
) -> None:
    async def scenario(world: World) -> None:
        document = world.add_document()
        keys = world.keys(document)
        deleted_at = datetime.now(UTC)
        world.delete(document, now=deleted_at)

        async with environment(world) as env:
            result = await tick(env, after=timedelta(days=89))
            assert (result.purged, result.skipped, result.failed) == (0, 0, 0)
            assert all(world.exists(k) for k in keys) and world.state(document) == "DELETED"

            with world.sessions() as s:  # still restorable on day 89
                restored = retention.restore_document(
                    s, USER, PROJECT, document, now=deleted_at + timedelta(days=89)
                )
            assert restored.state.value == "UPLOADED" and restored.deleted_at is None

            # The restore is respected on the next ticks, even long after the original date.
            result = await tick(env, after=timedelta(days=30))
        assert (result.purged, result.skipped, result.failed) == (0, 0, 0)
        assert world.state(document) == "UPLOADED" and all(world.exists(k) for k in keys)
        assert "DOCUMENT_PURGED" not in world.audit(document)

    run(scenario, tmp_path)


# --- T09: idempotent and resumable ---------------------------------------------------------------


def test_e4_9_t09_a_retry_after_a_partial_deletion_completes_with_one_audit_entry(
    tmp_path: Path,
) -> None:
    async def scenario(world: World) -> None:
        document = world.add_document()
        keys = world.keys(document)
        world.delete(document, now=datetime.now(UTC) - timedelta(days=91))
        # The derivative and the upload are two deletes; the storage dies after the first one.
        flaky = FlakyStorage(world.storage, fail_after=1, failures=1)

        async with environment(world, storage=flaky) as env:
            result = await tick(env)

        assert flaky.failures == 0 and flaky.attempts >= 3  # failed once, then did it all again
        assert (result.purged, result.failed) == (1, 0)
        assert not any(world.exists(k) for k in keys)
        assert world.state(document) == "PURGED"
        audit = world.audit(document)
        assert audit.count("DOCUMENT_PURGED") == 1 and audit.count("purge") == 1

    run(scenario, tmp_path)


def test_e4_9_t09_a_storage_outage_fails_the_run_visibly_and_the_next_run_finishes(
    tmp_path: Path,
) -> None:
    async def scenario(world: World) -> None:
        document = world.add_document()
        keys = world.keys(document)
        world.delete(document, now=datetime.now(UTC) - timedelta(days=91))
        down = FlakyStorage(world.storage, fail_after=0, failures=10_000)

        async with environment(world, storage=down) as env:
            with pytest.raises(Exception) as failure:
                await tick(env)
            assert "PurgeIncomplete" in repr(failure.value.__cause__)
            assert world.state(document) == "DELETED"  # nothing half-done in the database
            assert world.audit(document).count("DOCUMENT_PURGED") == 0

            purge_activities.configure(storage=world.storage)  # storage is back for the next day
            result = await tick(env, after=DAY)

        assert (result.purged, result.failed) == (1, 0)
        assert not any(world.exists(k) for k in keys) and world.state(document) == "PURGED"
        assert world.audit(document).count("DOCUMENT_PURGED") == 1

    run(scenario, tmp_path)


def test_e4_9_one_failing_document_does_not_block_the_others(tmp_path: Path) -> None:
    class PoisonedStorage(FlakyStorage):
        def delete(self, key: str) -> None:
            if self.poison in key:
                raise StorageUnavailable
            super().delete(key)

        poison = ""

    async def scenario(world: World) -> None:
        bad = world.add_document("bad.pdf", PDF + b"bad")
        good = world.add_document("good.pdf", PDF + b"good")
        for document in (bad, good):
            world.delete(document, now=datetime.now(UTC) - timedelta(days=91))
        storage = PoisonedStorage(world.storage, fail_after=0, failures=0)
        storage.poison = world.keys(bad)[0]

        async with environment(world, storage=storage) as env:
            with pytest.raises(Exception):  # noqa: B017 - the run is marked incomplete
                await tick(env)

        assert world.state(good) == "PURGED" and world.state(bad) == "DELETED"
        assert not any(world.exists(k) for k in world.keys(good))

    run(scenario, tmp_path)


def test_e4_9_shared_content_survives_the_purge_of_one_copy(tmp_path: Path) -> None:
    async def scenario(world: World) -> None:
        old = world.add_document("old.pdf", PDF)
        world.delete(old, now=datetime.now(UTC) - timedelta(days=91))
        new = world.add_document("again.pdf", PDF)  # same bytes, same content-addressed key
        shared = world.keys(new)[0]
        assert shared == world.keys(old)[0]

        async with environment(world) as env:
            result = await tick(env)

        assert result.purged == 1 and world.state(old) == "PURGED"
        assert world.exists(shared) and world.state(new) == "PARSED"
        assert not world.exists(world.keys(old)[1])  # the old copy's own derivative is gone

    run(scenario, tmp_path)


# --- the schedule --------------------------------------------------------------------------------


class FakeScheduleHandle:
    def __init__(self, schedule: Schedule) -> None:
        self.schedule = schedule
        self.updates = 0

    async def update(self, updater: Callable[[Any], Any]) -> None:
        current = SimpleNamespace(description=SimpleNamespace(schedule=self.schedule))
        self.schedule = updater(current).schedule
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
        assert schedule_id == PURGE_SCHEDULE_ID and self.handle is not None
        return self.handle


def test_e4_9_schedule_is_daily_at_0200_utc_on_the_ingest_queue() -> None:
    schedule = purge_schedule()
    assert PURGE_CRON == "0 2 * * *"
    assert schedule.spec.cron_expressions == ["0 2 * * *"] and not schedule.spec.time_zone_name
    action = schedule.action
    assert isinstance(action, ScheduleActionStartWorkflow)
    assert action.workflow == "PurgeDeleted" and action.task_queue == "sbam-ingest"
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP


def test_e4_9_the_ingest_worker_creates_the_schedule_once(tmp_path: Path) -> None:
    client = FakeScheduleClient()
    asyncio.run(ensure_purge_schedule(client))  # type: ignore[arg-type]
    asyncio.run(ensure_purge_schedule(client))  # type: ignore[arg-type]  # a second worker starts
    ((schedule_id, schedule),) = client.created
    assert schedule_id == "purge-deleted" and schedule.spec.cron_expressions == [PURGE_CRON]
    assert client.handle is not None and client.handle.updates == 1  # converged, not duplicated


def test_e4_9_an_existing_schedule_is_brought_up_to_date_and_stays_paused() -> None:
    stale = Schedule(
        action=ScheduleActionStartWorkflow(
            "PurgeDeleted", id="purge-deleted", task_queue="sbam-ingest"
        ),
        spec=ScheduleSpec(cron_expressions=["0 5 * * *"]),
        state=ScheduleState(paused=True, note="paused by an operator"),
    )
    client = FakeScheduleClient(stale)
    asyncio.run(ensure_purge_schedule(client))  # type: ignore[arg-type]
    assert client.handle is not None
    updated = client.handle.schedule
    assert updated.spec.cron_expressions == [PURGE_CRON]
    assert updated.state.paused is True and updated.state.note == "paused by an operator"


def test_e4_9_purge_without_storage_settings_fails_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(purge_activities, "_storage", None)
    for name in ("STORAGE_ROOT", "S3_BUCKET", "STORAGE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir("/")  # no .env to pick up
    with pytest.raises(ApplicationError) as failure:
        purge_activities._backend()
    assert failure.value.non_retryable
