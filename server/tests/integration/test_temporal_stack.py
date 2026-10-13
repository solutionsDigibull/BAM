"""E1.7 against the Compose stack: real Temporal, the six real workers, the real run mirror."""

import asyncio
import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.temporal.client import OrchestratorUnavailable, connect
from sbam_api.temporal.models import TemporalRun
from sbam_api.temporal.repository import TemporalRunRepository
from sbam_api.temporal.service import StartSpec, start_run
from sbam_contracts.runs import (
    PingResult,
    RunInput,
    SubjectType,
    TaskQueue,
    ping_workflow_id,
)
from sbam_workers.pipeline.ping import PingWorkflow
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Compose, port
from .test_db import url

pytestmark = pytest.mark.integration

TENANT = uuid.uuid4()
TEMPORAL = f"127.0.0.1:{port('TEMPORAL_PORT', 7233)}"


def ping_spec(queue: TaskQueue, project: uuid.UUID) -> StartSpec:
    return StartSpec(
        workflow="PingWorkflow",
        workflow_id=ping_workflow_id(queue, project),
        queue=queue,
        subject_type=SubjectType.PROJECT,
        subject_id=project,
        stage="ping",
        input=RunInput(
            tenant_id=TENANT, project_id=project, run_id=uuid.uuid4(), correlation_id="e1-7-int"
        ),
    )


def cleanup(sessions: sessionmaker[Session], project: uuid.UUID) -> None:
    with sessions() as session:
        repo = TemporalRunRepository(session, TenantContext(TENANT, project))
        for row in repo.list(include_deleted=True):
            session.delete(row)
        session.commit()


def ready_checks() -> dict[str, str]:
    api = f"http://127.0.0.1:{port('API_PORT', 8000)}/health/ready"
    try:
        with urllib.request.urlopen(api, timeout=5) as res:
            body = res.read()
    except urllib.error.HTTPError as err:  # 503 still carries the per-check report
        body = err.read()
    checks: dict[str, str] = json.loads(body)["checks"]
    return checks


def test_e1_7_t01_ping_on_all_six_queues_and_mirror_completed() -> None:
    sessions = make_sessionmaker(make_engine(url()))
    project = uuid.uuid4()

    async def scenario() -> tuple[list[uuid.UUID], list[PingResult]]:
        client = await connect(TEMPORAL, "sbam")
        runs = [await start_run(client, sessions, ping_spec(q, project)) for q in TaskQueue]
        handles = [
            client.get_workflow_handle_for(PingWorkflow.run, r.temporal_workflow_id) for r in runs
        ]
        results = await asyncio.gather(*(h.result() for h in handles))
        return [r.id for r in runs], list(results)

    try:
        run_ids, results = asyncio.run(asyncio.wait_for(scenario(), 120))
        assert [r.task_queue for r in results] == list(TaskQueue)
        deadline = time.monotonic() + 30  # the final mirror update lands just after the result
        while True:
            with sessions() as session:
                repo = TemporalRunRepository(session, TenantContext(TENANT, project))
                rows: list[TemporalRun | None] = [repo.get(i) for i in run_ids]
            states = [(r.state, r.current_stage) if r else None for r in rows]
            if states == [("COMPLETED", "done")] * 6 or time.monotonic() > deadline:
                break
            time.sleep(1)
        assert states == [("COMPLETED", "done")] * 6
    finally:
        cleanup(sessions, project)


def test_e1_7_t03_temporal_stopped_503_and_no_mirror_row(compose: Compose) -> None:
    sessions = make_sessionmaker(make_engine(url()))
    project = uuid.uuid4()
    assert compose("stop", "temporal").returncode == 0
    try:
        with pytest.raises(OrchestratorUnavailable):
            asyncio.run(_start_with_fresh_client(sessions, project))
        with sessions() as session:
            assert TemporalRunRepository(session, TenantContext(TENANT, project)).list() == []
        assert ready_checks()["temporal"] == "fail"
    finally:
        compose("start", "temporal")
        deadline = time.monotonic() + 180  # wait until the API sees Temporal again
        while time.monotonic() < deadline and ready_checks().get("temporal") != "ok":
            time.sleep(3)
        cleanup(sessions, project)
    assert ready_checks()["temporal"] == "ok"


async def _start_with_fresh_client(sessions: sessionmaker[Session], project: uuid.UUID) -> None:
    try:
        client = await connect(TEMPORAL, "sbam")
    except RuntimeError:
        raise OrchestratorUnavailable() from None
    await start_run(client, sessions, ping_spec(TaskQueue.INGEST, project))
