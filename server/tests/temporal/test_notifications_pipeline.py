"""E10.4 notifications from the pipeline: the real ProjectPipeline workflow (stub activities,
Temporal's time-skipping server, no Docker) drives the real #41 transitions; the in-app sink turns
each notification-worthy outbox row into `notifications` rows in SQLite."""

import asyncio
import uuid
from collections import Counter
from typing import Any

from notifications_db import add_project, add_user, notification_sessions, rows
from pipeline_db import new_project, registered_stand_ins
from sbam_api.audit.models import NotificationOutbox
from sbam_api.audit.outbox import dispatch_pending
from sbam_api.notifications import service
from sbam_api.notifications.sink import InAppSink
from sbam_contracts.pipeline import NotifyRequest, PipelineStage
from sbam_workers import mirror
from sbam_workers.pipeline_state import notify
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from temporalio.testing import ActivityEnvironment
from test_project_pipeline import TENANT, approve, environment, finish, generate, start, until


def run(scenario: Any) -> Any:
    sessions = notification_sessions()
    mirror.configure(sessions)
    with registered_stand_ins():
        return asyncio.run(scenario(sessions))


def test_e10_4_t02_temporal_one_notification_per_stage_per_recipient() -> None:
    world: dict[str, Any] = {}

    async def scenario(sessions: sessionmaker[Session]) -> None:
        fdes = [add_user(sessions, TENANT), add_user(sessions, TENANT)]
        bystander = add_user(sessions, TENANT)  # an FDE not assigned to this project
        project = new_project(sessions, TENANT)
        add_project(sessions, TENANT, members=fdes, project_id=project, name="Acme onboarding")
        world.update(sessions=sessions, fdes=fdes, bystander=bystander, project=project)
        async with environment() as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            assert (await finish(handle)).stage is PipelineStage.DONE

    run(scenario)
    sessions = world["sessions"]
    expected = Counter(
        {
            "STAGE_COMPLETED": 4,  # ingestion, knowledge, workflow created, Sub-Skills certified
            "SOW_READY": 1,
            "BREAKDOWN_READY": 1,
            "WORKFLOW_CERTIFIED": 1,
            "WORKFLOW_FROZEN": 1,
        }
    )
    for fde in world["fdes"]:
        mine = rows(sessions, fde)
        assert Counter(r.type for r in mine) == expected
        stage_titles = [r.title for r in mine if r.type == "STAGE_COMPLETED"]
        assert len(set(stage_titles)) == 4  # one per stage, none repeated
        assert all(r.project_id == world["project"] and r.read_at is None for r in mine)
        assert all(r.link and service.safe_link(r.link) == r.link for r in mine)
        assert all("Acme onboarding" in r.body for r in mine if r.type == "SOW_READY")
        frozen = next(r for r in mine if r.type == "WORKFLOW_FROZEN")
        assert frozen.workflow_id is not None and frozen.link.endswith("/versions")
    assert rows(sessions, world["bystander"]) == []
    with sessions() as session:  # every notification-worthy outbox row was dispatched once
        pending = session.scalars(
            select(NotificationOutbox).where(NotificationOutbox.dispatched_at.is_(None))
        ).all()
    assert pending == []


def failure(project: uuid.UUID, key: str) -> NotifyRequest:
    return NotifyRequest(
        tenant_id=TENANT,
        project_id=project,
        type="pipeline.failed",
        entity_type="project_pipeline",
        entity_id=project,
        payload={"stage": "SOW generation", "error": "model timed out", "next_action": "Retry."},
        correlation_id="cid-e10-4",
        idempotency_key=key,
    )


def test_e10_4_t03_temporal_idempotent_notify_retry_after_commit_adds_no_rows() -> None:
    sessions = notification_sessions()
    mirror.configure(sessions)
    fde = add_user(sessions, TENANT)
    project = new_project(sessions, TENANT)
    add_project(sessions, TENANT, members=[fde], project_id=project)
    req = failure(project, "run-1:failed:sow")
    ActivityEnvironment().run(notify, req)
    assert [r.type for r in rows(sessions, fde)] == ["GENERATION_FAILED"]
    ActivityEnvironment().run(notify, req)  # the activity retried after its commit
    assert len(rows(sessions, fde)) == 1
    # Worse: the process died after the notification committed but before the outbox row was
    # marked dispatched, so the same row is delivered again.
    with sessions() as session:
        session.execute(update(NotificationOutbox).values(dispatched_at=None))
        session.commit()
    assert dispatch_pending(sessions, InAppSink(sessions)) == 1
    [row] = rows(sessions, fde)
    assert "model timed out" in row.body and "SOW generation" in row.body


def test_e10_4_twenty_simultaneous_failures_notify_twenty_times() -> None:
    sessions = notification_sessions()
    mirror.configure(sessions)
    fde = add_user(sessions, TENANT)
    project = new_project(sessions, TENANT)
    add_project(sessions, TENANT, members=[fde], project_id=project)
    for i in range(20):  # 20 Sub-Skills fail: distinct idempotency keys, no throttling
        ActivityEnvironment().run(notify, failure(project, f"run-1:failed:sub-skill-{i}"))
    assert len(rows(sessions, fde)) == 20
