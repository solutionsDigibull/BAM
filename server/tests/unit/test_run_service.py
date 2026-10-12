"""E1.7 run starts + mirror: size guard, Temporal down, idempotent start, updates, reconciler."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from runs_db import FakeClient, age, all_rows, memory_sessions
from sbam_api.db.repository import TenantContext
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository
from sbam_api.temporal.service import PayloadTooLarge, StartSpec, start_run
from sbam_contracts.runs import (
    RunInput,
    RunState,
    SubjectType,
    TaskQueue,
    pipeline_workflow_id,
)
from sbam_workers import mirror
from temporalio.client import Client, WorkflowExecutionStatus

TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()


class BigInput(RunInput):
    blob: str


def spec(inp: RunInput | None = None) -> StartSpec:
    return StartSpec(
        workflow="ProjectPipeline",
        workflow_id=pipeline_workflow_id(PROJECT),
        queue=TaskQueue.INGEST,
        subject_type=SubjectType.PROJECT,
        subject_id=PROJECT,
        stage="ingest",
        input=inp
        or RunInput(
            tenant_id=TENANT, project_id=PROJECT, run_id=uuid.uuid4(), correlation_id="cid-1"
        ),
    )


def start(client: FakeClient, sessions: object, s: StartSpec | None = None) -> object:
    return asyncio.run(start_run(cast(Client, client), sessions, s or spec()))  # type: ignore[arg-type]


def test_e1_7_start_writes_running_mirror_row() -> None:
    client, sessions = FakeClient(), memory_sessions()
    run = start(client, sessions)
    [row] = all_rows(sessions)
    assert (row.state, row.current_stage, row.tenant_id, row.project_id) == (
        "RUNNING",
        "ingest",
        TENANT,
        PROJECT,
    )
    assert run.id == row.id and run.temporal_run_id == "run-1"  # type: ignore[attr-defined]
    call = client.calls[0]
    assert call["task_queue"] == "sbam-ingest"
    assert call["id_conflict_policy"].name == "USE_EXISTING"
    names = {p.key.name for p in call["search_attributes"]}
    assert names == {"TenantId", "ProjectId", "SbamStage"}


def test_e1_7_t03_temporal_down_503_and_no_mirror_row() -> None:
    client, sessions = FakeClient(down=True), memory_sessions()
    with pytest.raises(OrchestratorUnavailable) as err:
        start(client, sessions)
    assert (err.value.status, err.value.code) == (503, "ORCHESTRATOR_UNAVAILABLE")
    assert all_rows(sessions) == []


def test_e1_7_t04_duplicate_start_returns_existing_run() -> None:
    client, sessions = FakeClient(), memory_sessions()
    first, second = start(client, sessions), start(client, sessions)
    assert first == second
    assert len(all_rows(sessions)) == 1
    assert len(client.executions) == 1  # one execution, both starts used it


def test_e1_7_t07_payload_over_64_kib_rejected_before_start() -> None:
    client, sessions = FakeClient(), memory_sessions()
    big = BigInput(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=uuid.uuid4(),
        correlation_id="c",
        blob="x" * 100 * 1024,
    )
    with pytest.raises(PayloadTooLarge) as err:
        start(client, sessions, spec(big))
    assert (err.value.status, err.value.code) == (413, "PAYLOAD_TOO_LARGE")
    assert client.calls == [] and all_rows(sessions) == []


def test_e1_7_mirror_updates_are_idempotent_and_terminal_is_final() -> None:
    sessions = memory_sessions()
    start(FakeClient(), sessions)
    wf = pipeline_workflow_id(PROJECT)
    with sessions() as session:
        repo = TemporalRunRepository(session, TenantContext(TENANT, PROJECT))
        assert repo.apply(wf, "run-1", RunState.RUNNING, "knowledge", None)
        assert repo.apply(wf, "run-1", RunState.COMPLETED, "done", None)
        assert repo.apply(wf, "run-1", RunState.FAILED, "x", "late duplicate")  # ignored
        assert not repo.apply(wf, "run-404", RunState.COMPLETED, "done", None)  # no row (yet)
        session.commit()
    [row] = all_rows(sessions)
    assert (row.state, row.current_stage, row.last_error) == ("COMPLETED", "done", None)


def test_e1_7_mirror_is_tenant_scoped() -> None:
    sessions = memory_sessions()
    run = start(FakeClient(), sessions)
    with sessions() as session:
        other = TemporalRunRepository(session, TenantContext(uuid.uuid4(), PROJECT))
        assert other.get(run.id) is None  # type: ignore[attr-defined]
        assert not other.apply(pipeline_workflow_id(PROJECT), "run-1", RunState.FAILED, "", None)


def test_e1_7_reconciler_repairs_lost_updates() -> None:
    sessions = memory_sessions()
    mirror.configure(sessions)
    client = FakeClient()
    start(client, sessions)
    other_project = uuid.uuid4()
    start(
        client,
        sessions,
        StartSpec(
            workflow="ProjectPipeline",
            workflow_id=pipeline_workflow_id(other_project),
            queue=TaskQueue.INGEST,
            subject_type=SubjectType.PROJECT,
            subject_id=other_project,
            stage="ingest",
            input=RunInput(
                tenant_id=TENANT, project_id=other_project, run_id=uuid.uuid4(), correlation_id="c"
            ),
        ),
    )
    reconcile = mirror.reconcile(cast(Client, client))
    assert asyncio.run(reconcile) == 0  # rows are younger than the grace period
    age(sessions, datetime.now(UTC) - timedelta(minutes=5))
    client.statuses[pipeline_workflow_id(PROJECT)] = WorkflowExecutionStatus.COMPLETED
    client.missing.add(pipeline_workflow_id(other_project))
    assert asyncio.run(mirror.reconcile(cast(Client, client))) == 2
    states = {r.project_id: (r.state, r.last_error) for r in all_rows(sessions)}
    assert states[PROJECT] == ("COMPLETED", None)
    assert states[other_project] == ("FAILED", "execution not found in Temporal")
    assert asyncio.run(mirror.reconcile(cast(Client, client))) == 0  # nothing left RUNNING
