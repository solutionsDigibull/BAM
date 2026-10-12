"""E1.7 workflows against Temporal's time-skipping test server (TD §19.3; no Docker needed).

The server binary is downloaded once by temporalio on first use. Activities are the real ones; the
mirror uses an in-memory SQLite `temporal_runs`.
"""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from runs_db import all_rows, memory_sessions
from sbam_api.temporal.service import StartSpec, start_run
from sbam_contracts.runs import (
    PingResult,
    RunInput,
    SubjectType,
    TaskQueue,
    ping_workflow_id,
)
from sbam_workers import mirror
from sbam_workers.bootstrap import build_workers
from sbam_workers.ping import ping
from sbam_workers.pipeline.ping import PingWorkflow
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.client import Client, WorkflowHistory
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

HISTORY = Path(__file__).parent / "histories" / "ping_workflow.json"
TENANT = uuid.uuid4()


def ping_spec(queue: TaskQueue, project: uuid.UUID) -> StartSpec:
    return StartSpec(
        workflow="PingWorkflow",
        workflow_id=ping_workflow_id(queue, project),
        queue=queue,
        subject_type=SubjectType.PROJECT,
        subject_id=project,
        stage="ping",
        input=RunInput(
            tenant_id=TENANT, project_id=project, run_id=uuid.uuid4(), correlation_id="cid-e1-7"
        ),
    )


@asynccontextmanager
async def environment(**client_kwargs: Any) -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter, **client_kwargs
    )
    async with env:
        yield env


@asynccontextmanager
async def workers(client: Client, queues: list[TaskQueue]) -> AsyncIterator[None]:
    with ThreadPoolExecutor(max_workers=8) as executor:
        async with AsyncExitStack() as stack:
            for worker in build_workers(client, queues, executor):
                await stack.enter_async_context(worker)
            yield


async def run_ping(
    client: Client, sessions: sessionmaker[Session], queue: TaskQueue, project: uuid.UUID
) -> PingResult:
    run = await start_run(client, sessions, ping_spec(queue, project), with_search_attributes=False)
    handle = client.get_workflow_handle_for(PingWorkflow.run, run.temporal_workflow_id)
    return await handle.result()


def test_e1_7_t01_ping_completes_on_all_six_queues() -> None:
    sessions = memory_sessions()
    mirror.configure(sessions)

    async def scenario() -> list[PingResult]:
        async with environment() as env, workers(env.client, list(TaskQueue)):
            project = uuid.uuid4()
            return list(
                await asyncio.gather(
                    *(run_ping(env.client, sessions, q, project) for q in TaskQueue)
                )
            )

    results = asyncio.run(scenario())
    assert [r.task_queue for r in results] == list(TaskQueue)  # each answered on its own queue
    rows = all_rows(sessions)
    assert len(rows) == 6
    assert {(r.state, r.current_stage) for r in rows} == {("COMPLETED", "done")}


@activity.defn(name="ping")
async def dying_ping(inp: RunInput) -> PingResult:
    """Stands in for a worker that dies mid-activity: heartbeats once, then hangs."""
    activity.heartbeat("started")
    STARTED.set()
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


STARTED = asyncio.Event()


def test_e1_7_t05_worker_crash_mid_activity_is_retried_elsewhere() -> None:
    sessions = memory_sessions()
    mirror.configure(sessions)

    async def scenario() -> PingResult:
        global STARTED
        STARTED = asyncio.Event()
        queue, project = TaskQueue.BUILD, uuid.uuid4()
        async with environment() as env:
            client = env.client
            with ThreadPoolExecutor(max_workers=4) as executor:
                # Workflow tasks only (no activity polling), up for the whole scenario.
                async with Worker(client, task_queue=queue.value, workflows=[PingWorkflow]):
                    run = await start_run(
                        client, sessions, ping_spec(queue, project), with_search_attributes=False
                    )
                    # Worker A takes the ping activity, then dies mid-activity.
                    async with Worker(client, task_queue=queue.value, activities=[dying_ping]):
                        await asyncio.wait_for(STARTED.wait(), 30)
                    # Worker B (healthy) gets the retry once the heartbeat timeout fires.
                    healthy = Worker(
                        client,
                        task_queue=queue.value,
                        activities=[ping, mirror.update_run_mirror],
                        activity_executor=executor,
                    )
                    async with healthy:
                        handle = client.get_workflow_handle_for(
                            PingWorkflow.run, run.temporal_workflow_id
                        )
                        return await handle.result()

    result = asyncio.run(scenario())
    assert result.attempt >= 2
    [row] = all_rows(sessions)
    assert row.state == "COMPLETED"


def test_e1_7_t08_activity_span_shares_the_api_trace() -> None:
    sessions = memory_sessions()
    mirror.configure(sessions)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("e1-7-t08")

    async def scenario() -> int:
        async with environment(interceptors=[TracingInterceptor(tracer)]) as env:
            async with workers(env.client, [TaskQueue.LLM]):
                with tracer.start_as_current_span("POST /api/v1/projects/{id}/runs/ping") as span:
                    await run_ping(env.client, sessions, TaskQueue.LLM, uuid.uuid4())
                    return span.get_span_context().trace_id

    trace_id = asyncio.run(scenario())
    spans = exporter.get_finished_spans()
    activity_spans = [s for s in spans if s.name == "RunActivity:ping"]
    assert activity_spans, sorted({s.name for s in spans})
    assert all(s.context is not None and s.context.trace_id == trace_id for s in activity_spans)


def test_e1_7_ping_workflow_replays_from_recorded_history() -> None:
    """Determinism guard: a change to PingWorkflow that breaks old histories fails here."""
    workflow_id = ping_workflow_id(TaskQueue.INGEST, uuid.UUID(int=0))  # any valid ping id
    history = WorkflowHistory.from_json(workflow_id, json.loads(HISTORY.read_text("utf-8")))
    replayer = Replayer(workflows=[PingWorkflow], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_ping_history() -> None:  # pragma: no cover - maintenance helper
    sessions = memory_sessions()
    mirror.configure(sessions)

    async def scenario() -> dict[str, Any]:
        async with environment() as env, workers(env.client, [TaskQueue.INGEST]):
            run = await start_run(
                env.client,
                sessions,
                ping_spec(TaskQueue.INGEST, uuid.uuid4()),
                with_search_attributes=False,
            )
            handle = env.client.get_workflow_handle(run.temporal_workflow_id)
            await handle.result()
            history = await handle.fetch_history()
            data: dict[str, Any] = json.loads(history.to_json())
            return data

    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(asyncio.run(scenario()), indent=1) + "\n", encoding="utf-8")
