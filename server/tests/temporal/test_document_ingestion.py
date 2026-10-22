"""E4.3 and E4.4 on Temporal's time-skipping test server: the scan's retry policy, what the
workflow does with each verdict, and the type detection step after a CLEAN scan. The activities
are fakes with the real names; the real scan and detection are in tests/unit.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sbam_api.core.ids import uuid7
from sbam_contracts.documents import ScanOutcome, ScanStatus
from sbam_contracts.pipeline import IdsResult, StageRef
from sbam_workers.ingest.document_ingestion import SCAN_ATTEMPTS, DocumentIngestion
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

QUEUE = "sbam-ingest"


@dataclass
class Fakes:
    """Counts what the workflow asked for. `fail_first` scan attempts raise; then `verdict`."""

    fail_first: int = 0
    verdict: ScanStatus = ScanStatus.CLEAN
    error: Exception = field(default_factory=lambda: ConnectionError("clamd is down"))
    scan_times: list[datetime] = field(default_factory=list)
    marked: int = 0
    # E4.4: what `detect_type` answers ("pass" hands the version on, "reject" ends it), how many
    # attempts raise first, and how many times it was called.
    detect: str = "pass"
    detect_fail_first: int = 0
    detect_error: Exception = field(default_factory=lambda: ConnectionError("storage is down"))
    detect_calls: int = 0


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

    @activity.defn(name="detect_type")
    async def detect_type(inp: StageRef) -> IdsResult:
        fakes.detect_calls += 1
        if fakes.detect_calls <= fakes.detect_fail_first:
            raise fakes.detect_error
        return IdsResult(ids=[inp.ref_id] if fakes.detect == "pass" else [])

    return [scan_clamav, mark_scan_failed, detect_type]


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


# --- E4.4: type detection after a CLEAN scan ----------------------------------------------------


def test_e4_4_a_clean_version_goes_through_detection_and_is_handed_on() -> None:
    fakes = Fakes()
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert fakes.detect_calls == 1


def test_e4_4_a_rejected_type_ends_the_execution_and_hands_nothing_on() -> None:
    fakes = Fakes(detect="reject")
    assert execute(fakes, stage_ref()) == []
    assert fakes.detect_calls == 1 and fakes.marked == 0  # rejected, not "scan failed"


@pytest.mark.parametrize("verdict", [ScanStatus.INFECTED, ScanStatus.SCAN_FAILED])
def test_e4_4_detection_only_runs_after_a_clean_scan(verdict: ScanStatus) -> None:
    fakes = Fakes(verdict=verdict)
    assert execute(fakes, stage_ref()) == []
    assert fakes.detect_calls == 0


def test_e4_4_detection_is_not_run_when_the_scan_never_succeeded() -> None:
    fakes = Fakes(fail_first=99)
    assert execute(fakes, stage_ref()) == []
    assert fakes.detect_calls == 0 and fakes.marked == 1


@pytest.mark.parametrize("failures", [1, 2])
def test_e4_4_a_transient_detection_error_is_retried(failures: int) -> None:
    fakes = Fakes(detect_fail_first=failures)
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert fakes.detect_calls == failures + 1


@pytest.mark.parametrize("kind", ["NotFound", "NotDetectable", "NotScanned"])
def test_e4_4_a_permanent_detection_problem_is_not_retried(kind: str) -> None:
    error = ApplicationError("no", type=kind, non_retryable=True)
    fakes = Fakes(detect_fail_first=99, detect_error=error)
    with pytest.raises(WorkflowFailureError):
        execute(fakes, stage_ref())
    assert fakes.detect_calls == 1


def test_e4_4_an_execution_started_before_detection_existed_still_replays() -> None:
    """Determinism guard (TD §13.4): `before_e4_4` was recorded with the workflow as it was when
    the scan handed a CLEAN version straight on; the `e4-4-detect-type` patch keeps it
    replayable."""
    path = Path(__file__).parent / "histories" / "document_ingestion_before_e4_4.json"
    history = WorkflowHistory.from_json(
        f"ingest-{uuid.UUID(int=1)}", json.loads(path.read_text("utf-8"))
    )
    replayer = Replayer(workflows=[DocumentIngestion], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))
