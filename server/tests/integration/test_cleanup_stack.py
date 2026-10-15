"""E4.12 against the Compose stack: the CleanupAbandonedUploads Schedule and a real run.

Abandoned uploads are made as the upload path makes them: a reservation row in Postgres and staged
files in the storage volume (written from inside `worker-ingest`, the container that mounts it).
The workflow is started the way the 03:00 schedule starts it, and the files are checked from inside
the container again. The schedule itself is only read and converged, never deleted.
"""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sbam_api.ingest.models import UploadReservation
from sbam_api.ingest.rules import ABANDONED_ROW_RETENTION
from sbam_api.temporal.client import connect
from sbam_contracts.documents import (
    CLEANUP_HOUR_UTC,
    CLEANUP_SCHEDULE_ID,
    CLEANUP_UPLOADS,
    CleanupResult,
)
from sbam_workers.ingest.cleanup_schedule import ensure_cleanup_schedule
from sqlalchemy import select
from temporalio.client import ScheduleActionStartWorkflow, ScheduleRange

from .conftest import Compose, port
from .test_uploads_stack import Scope

pytestmark = pytest.mark.integration

TEMPORAL = f"127.0.0.1:{port('TEMPORAL_PORT', 7233)}"
STAGING = "/data/storage/uploads"


@pytest.fixture
def scope() -> Iterator[Scope]:
    s = Scope()
    try:
        yield s
    finally:
        s.drop()


def reservation(scope: Scope, status: str, hold_ends: timedelta) -> uuid.UUID:
    """A reservation whose hold ends `hold_ends` from now (negative: it ended that long ago)."""
    row = UploadReservation(
        tenant_id=scope.tenant,
        project_id=scope.project,
        batch_id=uuid.uuid4(),
        user_id=scope.user,
        name="spec.pdf",
        size=10,
        mime="application/pdf",
        status=status,
        expires_at=datetime.now(UTC) + hold_ends,
    )
    with scope.sessions() as s:
        s.add(row)
        s.commit()
        return row.id


def stage(compose: Compose, rid: uuid.UUID) -> None:
    command = f"printf partial > {STAGING}/{rid} && printf '{{}}' > {STAGING}/{rid}.info"
    assert compose("exec", "-T", "worker-ingest", "sh", "-c", command).returncode == 0


def staged(compose: Compose, rid: uuid.UUID) -> set[str]:
    listing = compose("exec", "-T", "worker-ingest", "ls", STAGING).stdout.split()
    return {name for name in listing if name.startswith(str(rid))}


def run_cleanup() -> CleanupResult:
    async def scenario() -> CleanupResult:
        client = await connect(TEMPORAL, "sbam")
        result: CleanupResult = await client.execute_workflow(
            CLEANUP_UPLOADS,
            id=f"{CLEANUP_SCHEDULE_ID}-it-{uuid.uuid4()}",
            task_queue="sbam-ingest",
            result_type=CleanupResult,
        )
        return result

    return asyncio.run(scenario())


def rows(scope: Scope) -> set[uuid.UUID]:
    with scope.sessions() as s:
        return set(
            s.scalars(
                select(UploadReservation.id).where(UploadReservation.tenant_id == scope.tenant)
            )
        )


def test_e4_12_t01_an_abandoned_upload_loses_its_staged_files(
    scope: Scope, compose: Compose
) -> None:
    rid = reservation(scope, "CREATED", -timedelta(hours=1))
    stage(compose, rid)
    assert len(staged(compose, rid)) == 2
    result = run_cleanup()
    assert result.files_removed >= 2 and result.failed == 0
    assert staged(compose, rid) == set()
    assert rid in rows(scope)  # the row stays until its retention is over


def test_e4_12_t02_an_upload_inside_its_hold_is_untouched(scope: Scope, compose: Compose) -> None:
    rid = reservation(scope, "CREATED", timedelta(hours=2))
    stage(compose, rid)
    run_cleanup()
    assert len(staged(compose, rid)) == 2 and rid in rows(scope)


def test_e4_12_t03_a_finished_upload_is_untouched(scope: Scope, compose: Compose) -> None:
    rid = reservation(scope, "FINISHED", -(ABANDONED_ROW_RETENTION + timedelta(days=30)))
    run_cleanup()
    assert rid in rows(scope)


def test_e4_12_t05_old_rows_are_deleted_and_young_ones_kept(scope: Scope) -> None:
    old = reservation(scope, "RESERVED", -(ABANDONED_ROW_RETENTION + timedelta(days=1)))
    young = reservation(scope, "RESERVED", -(ABANDONED_ROW_RETENTION - timedelta(days=1)))
    result = run_cleanup()
    assert result.rows_deleted >= 1
    assert rows(scope) == {young} and old not in rows(scope)


def test_e4_12_the_schedule_is_daily_at_0300_utc_on_the_real_server() -> None:
    async def scenario() -> None:
        client = await connect(TEMPORAL, "sbam")
        await ensure_cleanup_schedule(client)  # what every ingest worker does on start
        await ensure_cleanup_schedule(client)  # idempotent
        schedule = (await client.get_schedule_handle(CLEANUP_SCHEDULE_ID).describe()).schedule
        # Temporal reads a cron string back as calendars (the string is gone), so the schedule is
        # defined and compared as a calendar.
        (calendar,) = schedule.spec.calendars
        assert list(calendar.hour) == [ScheduleRange(CLEANUP_HOUR_UTC)]
        assert list(calendar.minute) == [ScheduleRange(0)]
        action = schedule.action
        assert isinstance(action, ScheduleActionStartWorkflow)
        assert action.workflow == CLEANUP_UPLOADS and action.task_queue == "sbam-ingest"

    asyncio.run(scenario())
