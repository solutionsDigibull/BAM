"""E3.8b on Temporal's time-skipping test server: what `MasterDocumentIngestion` does with the parse
step's answers, its retry policy and its timeout. The activities are fakes with the real names; the
real ones are in tests/unit/test_master_document_parse.py."""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sbam_api.core.ids import uuid7
from sbam_contracts.api import ErrorCode
from sbam_contracts.pipeline import IdsResult, StageRef
from sbam_workers.ingest import master_document_ingestion
from sbam_workers.ingest.master_document_ingestion import MasterDocumentIngestion
from sbam_workers.ingest.parsing import ParseFailure
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

QUEUE = "sbam-ingest"


@dataclass
class Fakes:
    """What `parse_master_document` answers ("parsed" or "failed" = the file's own verdict), how
    many attempts raise first, how many hang (no heartbeat), and what the workflow asked for."""

    parse: str = "parsed"
    fail_first: int = 0
    error: Exception = field(default_factory=lambda: ConnectionError("storage is down"))
    hang_first: int = 0
    calls: int = 0
    times: list[datetime] = field(default_factory=list)
    gave_up: list[ErrorCode] = field(default_factory=list)


def activities(fakes: Fakes) -> list[Callable[..., Any]]:
    @activity.defn(name="parse_master_document")
    async def parse_master_document(inp: StageRef) -> IdsResult:
        fakes.calls += 1
        fakes.times.append(activity.info().started_time)
        if fakes.calls <= fakes.hang_first:
            await asyncio.sleep(30)  # a worker that died mid-parse: no heartbeat arrives
        if fakes.calls <= fakes.fail_first:
            raise fakes.error
        return IdsResult(ids=[inp.ref_id] if fakes.parse == "parsed" else [])

    @activity.defn(name="mark_master_document_failed")
    async def mark_master_document_failed(failure: ParseFailure) -> IdsResult:
        fakes.gave_up.append(failure.code)
        return IdsResult(ids=[])

    return [parse_master_document, mark_master_document_failed]


@asynccontextmanager
async def running(fakes: Fakes, *, sandbox: bool = True) -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    async with env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[MasterDocumentIngestion],
            activities=activities(fakes),
            workflow_runner=SandboxedWorkflowRunner() if sandbox else UnsandboxedWorkflowRunner(),
        ):
            yield env


def stage_ref() -> StageRef:
    return StageRef(
        tenant_id=uuid.uuid4(),
        project_id=uuid.UUID(int=0),
        run_id=uuid7(),
        correlation_id="e3-8b-temporal",
        ref_id=uuid7(),
    )


def execute(fakes: Fakes, inp: StageRef, *, sandbox: bool = True) -> list[uuid.UUID]:
    async def scenario() -> list[uuid.UUID]:
        async with running(fakes, sandbox=sandbox) as env:
            result = await env.client.execute_workflow(
                MasterDocumentIngestion.run,
                inp,
                id=f"master-doc-{inp.ref_id}",
                task_queue=QUEUE,
            )
            return result.ids

    return asyncio.run(scenario())


def test_e3_8b_a_parsed_document_is_handed_back() -> None:
    fakes, inp = Fakes(), stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert fakes.calls == 1 and fakes.gave_up == []


def test_e3_8b_a_file_the_loader_cannot_read_ends_normally_with_nothing() -> None:
    fakes = Fakes(parse="failed")
    assert execute(fakes, stage_ref()) == []
    assert fakes.calls == 1 and fakes.gave_up == []  # the file's own verdict: no retry


@pytest.mark.parametrize("failures", [1, 2])
def test_e3_8b_a_transient_error_is_retried_with_backoff(failures: int) -> None:
    fakes, inp = Fakes(fail_first=failures), stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert fakes.calls == failures + 1 and fakes.gave_up == []
    assert fakes.times[1] - fakes.times[0] >= timedelta(seconds=10)
    if failures == 2:
        assert fakes.times[2] - fakes.times[1] >= timedelta(seconds=20)


def test_e3_8b_after_three_failed_attempts_the_document_is_marked_parse_error() -> None:
    fakes = Fakes(fail_first=99)
    assert execute(fakes, stage_ref()) == []
    assert fakes.calls == 3 and fakes.gave_up == [ErrorCode.PARSE_ERROR]


def test_e3_8b_a_worker_that_dies_mid_parse_is_retried_after_the_heartbeat_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master_document_ingestion, "PARSE_HEARTBEAT_SECONDS", 1)
    fakes, inp = Fakes(hang_first=1), stage_ref()
    assert execute(fakes, inp, sandbox=False) == [inp.ref_id]
    assert fakes.calls == 2 and fakes.gave_up == []  # retried, then parsed once


def test_e3_8b_a_parse_that_never_beats_its_heart_ends_as_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master_document_ingestion, "PARSE_HEARTBEAT_SECONDS", 1)
    fakes = Fakes(hang_first=99)
    assert execute(fakes, stage_ref(), sandbox=False) == []
    assert fakes.calls == 3 and fakes.gave_up == [ErrorCode.TIMEOUT]


def test_e3_8b_the_parse_limits_are_the_ones_of_an_upload() -> None:
    from sbam_workers.ingest import document_ingestion

    mine, theirs = master_document_ingestion.PARSE_RETRY, document_ingestion.PARSE_RETRY
    assert mine.maximum_attempts == theirs.maximum_attempts == 3
    assert mine.initial_interval == theirs.initial_interval == timedelta(seconds=10)
    assert mine.backoff_coefficient == theirs.backoff_coefficient == 2.0


@pytest.mark.parametrize("kind", ["NotFound", "NotScanned"])
def test_e3_8b_a_permanent_problem_is_not_retried_and_not_marked(kind: str) -> None:
    error = ApplicationError("no", type=kind, non_retryable=True)
    fakes = Fakes(fail_first=99, error=error)
    with pytest.raises(WorkflowFailureError):
        execute(fakes, stage_ref())
    assert fakes.calls == 1 and fakes.gave_up == []


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_master_document_ingestion_history() -> None:  # pragma: no cover
    """Maintenance helper (`make record-histories`): one parsed master document.
    `test_replay_histories.py` replays it."""
    fakes, inp = Fakes(), stage_ref()

    async def scenario() -> dict[str, Any]:
        async with running(fakes) as env:
            handle = await env.client.start_workflow(
                MasterDocumentIngestion.run,
                inp,
                id=f"master-doc-{inp.ref_id}",
                task_queue=QUEUE,
            )
            await handle.result()
            data: dict[str, Any] = json.loads((await handle.fetch_history()).to_json())
            return data

    out = Path(__file__).parent / "histories" / "master_document_ingestion.json"
    out.write_text(json.dumps(asyncio.run(scenario()), indent=1) + "\n", encoding="utf-8")
