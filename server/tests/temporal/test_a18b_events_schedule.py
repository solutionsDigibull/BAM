"""A-18b `ImportKeycloakEvents`: the workflow hands the tick's time to its activity, the schedule is
every five minutes and never overlaps, and without the service account secret the activity does
nothing instead of failing."""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sbam_contracts.runs import TaskQueue
from sbam_workers import registry
from sbam_workers.identity import keycloak_events as worker
from sbam_workers.identity.keycloak_events import (
    IMPORT_SCHEDULE_ID,
    IMPORT_WORKFLOW,
    ImportKeycloakEvents,
    ImportOutcome,
    ensure_import_schedule,
    feed_from,
    import_schedule,
)
from sbam_workers.settings import WorkerSettings
from temporalio import activity
from temporalio.client import ScheduleAlreadyRunningError, ScheduleOverlapPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker


def settings(**over: Any) -> WorkerSettings:
    values: dict[str, Any] = {"temporal_host": "t:7233", "database_url": "postgresql://x/y", **over}
    return WorkerSettings(_env_file=None, **values)  # type: ignore[call-arg]


def test_a18b_the_workflow_passes_the_tick_time_to_the_activity() -> None:
    seen: list[datetime] = []

    @activity.defn(name="import_keycloak_events")
    def stand_in(as_of: datetime) -> ImportOutcome:
        seen.append(as_of)
        return ImportOutcome(read=4, written=3, already=1)

    async def go() -> ImportOutcome:
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env:
            async with Worker(
                env.client,
                task_queue=TaskQueue.INGEST.value,
                workflows=[ImportKeycloakEvents],
                activities=[stand_in],
                activity_executor=ThreadPoolExecutor(2),
            ):
                out: ImportOutcome = await env.client.execute_workflow(
                    IMPORT_WORKFLOW,
                    id=f"kc-{uuid.uuid4()}",
                    task_queue=TaskQueue.INGEST.value,
                    result_type=ImportOutcome,
                )
                return out

    outcome = asyncio.run(go())
    assert (outcome.read, outcome.written) == (4, 3)
    assert len(seen) == 1 and seen[0].tzinfo is not None


def test_a18b_the_schedule_is_every_five_minutes_and_never_overlaps() -> None:
    schedule = import_schedule()
    [interval] = schedule.spec.intervals
    assert interval.every == timedelta(minutes=5)
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
    action: Any = schedule.action
    assert action.workflow == IMPORT_WORKFLOW and action.task_queue == TaskQueue.INGEST.value


def test_a18b_it_registers_on_the_ingest_queue_only() -> None:
    registry.load_all()
    workflows, activities = registry.collect(TaskQueue.INGEST)
    assert ImportKeycloakEvents in workflows and worker.import_events in activities
    for queue in TaskQueue:
        if queue is not TaskQueue.INGEST:
            assert ImportKeycloakEvents not in registry.collect(queue)[0]
    assert ensure_import_schedule in registry._STARTUP[TaskQueue.INGEST]


def test_a18b_without_the_secret_or_url_there_is_no_feed() -> None:
    assert feed_from(settings()) is None
    assert feed_from(settings(keycloak_admin_client_secret="s")) is None  # noqa: S106
    assert feed_from(settings(keycloak_internal_url="http://keycloak:8080/realms/bullm")) is None
    feed = feed_from(
        settings(
            keycloak_internal_url="http://keycloak:8080/realms/bullm/",
            keycloak_admin_client_secret=SecretStr("s"),
        )
    )
    assert feed is not None and feed.admin_url == "http://keycloak:8080/admin/realms/bullm"


def test_a18b_the_activity_does_nothing_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMPORAL_HOST", "t:7233")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.delenv("KEYCLOAK_ADMIN_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("KEYCLOAK_INTERNAL_URL", raising=False)
    out = worker.import_events(datetime.now().astimezone())
    assert out.skipped == "not configured" and out.written == 0


def test_a18b_a_second_worker_converges_the_existing_schedule() -> None:
    calls: list[str] = []

    class Handle:
        async def update(self, fn: Any) -> None:
            calls.append("update")

    class Client:
        def __init__(self, exists: bool) -> None:
            self.exists = exists

        async def create_schedule(self, schedule_id: str, schedule: Any) -> None:
            assert schedule_id == IMPORT_SCHEDULE_ID
            if self.exists:
                raise ScheduleAlreadyRunningError()
            calls.append("create")

        def get_schedule_handle(self, schedule_id: str) -> Handle:
            return Handle()

    asyncio.run(ensure_import_schedule(Client(False)))  # type: ignore[arg-type]
    asyncio.run(ensure_import_schedule(Client(True)))  # type: ignore[arg-type]
    assert calls == ["create", "update"]
