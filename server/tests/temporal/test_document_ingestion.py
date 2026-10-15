"""E4.3 on Temporal's time-skipping test server: the scan's retry policy and what the workflow does
with each verdict. The activities are fakes with the real names; the real scan is in tests/unit.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from sbam_api.core.ids import uuid7
from sbam_contracts.documents import ScanOutcome, ScanStatus
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest.document_ingestion import SCAN_ATTEMPTS, DocumentIngestion
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

QUEUE = "sbam-ingest"


@dataclass
class Fakes:
    """Counts what the workflow asked for. `fail_first` scan attempts raise; then `verdict`."""

    fail_first: int = 0
    verdict: ScanStatus = ScanStatus.CLEAN
    error: Exception = field(default_factory=lambda: ConnectionError("clamd is down"))
    scan_times: list[datetime] = field(default_factory=list)
    marked: int = 0


def activities(fakes: Fakes) -> list[Callable[..., Any]]:
    @activity.defn(name="scan_clamav")
    async def scan_clamav(inp: StageRef) -> ScanOutcome:
        fakes.scan_times.append(activity.info().started_time)  # test-server time, skipped ahead
        if len(fakes.scan_times) <= fakes.fail_first:
            raise fakes.error
        return ScanOutcome(document_version_id=inp.ref_id, verdict=fakes.verdict)

    @activity.defn(name="mark_scan_failed")
    async def mark_scan_failed(inp: StageRef) -> ScanOutcome:
        fakes.marked += 1
        return ScanOutcome(document_version_id=inp.ref_id, verdict=ScanStatus.SCAN_FAILED)

    return [scan_clamav, mark_scan_failed]


@asynccontextmanager
async def running(fakes: Fakes) -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    async with env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[DocumentIngestion],
            activities=activities(fakes),
        ):
            yield env


def stage_ref() -> StageRef:
    return StageRef(
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        run_id=uuid7(),
        correlation_id="e4-3-temporal",
        ref_id=uuid7(),
    )


def execute(fakes: Fakes, inp: StageRef) -> list[uuid.UUID]:
    """Run one DocumentIngestion on a fresh time-skipping server; return the ids it hands on."""

    async def scenario() -> list[uuid.UUID]:
        async with running(fakes) as env:
            result = await env.client.execute_workflow(
                DocumentIngestion.run, inp, id=f"ingest-{inp.ref_id}", task_queue=QUEUE
            )
            return result.ids

    return asyncio.run(scenario())


def test_e4_3_t03_clamd_down_three_attempts_with_backoff_then_scan_failed() -> None:
    fakes = Fakes(fail_first=99)
    inp = stage_ref()
    assert execute(fakes, inp) == []  # the workflow ends normally: the pipeline goes on
    assert SCAN_ATTEMPTS == 3 and len(fakes.scan_times) == 3
    first_wait = fakes.scan_times[1] - fakes.scan_times[0]
    second_wait = fakes.scan_times[2] - fakes.scan_times[1]
    assert first_wait >= timedelta(seconds=5) and second_wait >= timedelta(seconds=10)
    assert fakes.marked == 1


@pytest.mark.parametrize("failures", [1, 2])
def test_e4_3_t04_clamd_recovers_and_the_scan_succeeds_on_a_later_attempt(failures: int) -> None:
    fakes = Fakes(fail_first=failures)
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]  # clean: on to parsing
    assert len(fakes.scan_times) == failures + 1 and fakes.marked == 0


def test_e4_3_infected_is_not_retried_and_ends_the_execution() -> None:
    fakes = Fakes(verdict=ScanStatus.INFECTED)
    assert execute(fakes, stage_ref()) == []
    assert len(fakes.scan_times) == 1 and fakes.marked == 0


@pytest.mark.parametrize("kind", ["NotFound", "NotScannable"])
def test_e4_3_a_permanent_problem_is_not_retried_and_not_marked(kind: str) -> None:
    fakes = Fakes(fail_first=99, error=ApplicationError("gone", type=kind, non_retryable=True))
    with pytest.raises(WorkflowFailureError):
        execute(fakes, stage_ref())
    assert len(fakes.scan_times) == 1 and fakes.marked == 0
