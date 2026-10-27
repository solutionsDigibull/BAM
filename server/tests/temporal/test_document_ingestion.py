"""E4.3 to E4.5 on Temporal's time-skipping test server: the scan's retry policy, what the workflow
does with each verdict, the type detection step after a CLEAN scan and the parse step after it.
The activities are fakes with the real names; the real ones are in tests/unit.
"""

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
from sbam_contracts.documents import ParseOutcome, ParseReport, ScanOutcome, ScanStatus
from sbam_contracts.pipeline import IdsResult, StageRef
from sbam_workers.ingest import document_ingestion
from sbam_workers.ingest.document_ingestion import SCAN_ATTEMPTS, DocumentIngestion
from sbam_workers.ingest.parsing import ParseFailure
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

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
    # E4.5: what `parse_document` answers ("parsed", "failed" = the file's own verdict,
    # "no_loader"), how many attempts raise first, how many hang (no heartbeat), what it was asked.
    parse: str = "parsed"
    parse_fail_first: int = 0
    parse_error: Exception = field(default_factory=lambda: ConnectionError("storage is down"))
    parse_hang_first: int = 0
    parse_calls: int = 0
    parse_times: list[datetime] = field(default_factory=list)
    gave_up: list[ErrorCode] = field(default_factory=list)
    # E4.8: `parse` may be "ocr" (pages wait for the vision model); what `ocr_document` answers
    # ("parsed" or "failed"), how many attempts raise first, how many hang, what it was asked.
    ocr: str = "parsed"
    ocr_fail_first: int = 0
    ocr_error: Exception = field(default_factory=lambda: ConnectionError("Bifrost is down"))
    ocr_hang_first: int = 0
    ocr_calls: int = 0
    ocr_times: list[datetime] = field(default_factory=list)
    ocr_gave_up: list[ErrorCode] = field(default_factory=list)
    # E4.6: the child documents `register_attachments` makes for the first version it is asked
    # about (a child's own call finds none), which children's scan fails for good, and the calls.
    child_ids: list[uuid.UUID] = field(default_factory=list)
    failing_children: set[uuid.UUID] = field(default_factory=set)
    register_calls: list[uuid.UUID] = field(default_factory=list)
    finish_calls: int = 0


def activities(fakes: Fakes) -> list[Callable[..., Any]]:
    @activity.defn(name="scan_clamav")
    async def scan_clamav(inp: StageRef) -> ScanOutcome:
        fakes.scan_times.append(activity.info().started_time)  # test-server time, skipped ahead
        if inp.ref_id in fakes.failing_children:
            raise ApplicationError("gone", type="NotFound", non_retryable=True)
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

    @activity.defn(name="parse_document")
    async def parse_document(inp: StageRef) -> ParseOutcome:
        fakes.parse_calls += 1
        fakes.parse_times.append(activity.info().started_time)
        if fakes.parse_calls <= fakes.parse_hang_first:
            await asyncio.sleep(30)  # a worker that died mid-parse: no heartbeat arrives
        if fakes.parse_calls <= fakes.parse_fail_first:
            raise fakes.parse_error
        if fakes.parse == "failed":
            return ParseOutcome(
                document_version_id=inp.ref_id, parsed=False, code=ErrorCode.CORRUPT
            )
        if fakes.parse == "no_loader":
            return ParseOutcome(document_version_id=inp.ref_id, parsed=False)
        if fakes.parse == "ocr":  # the text pass is done; pages wait for the vision model
            waiting = ParseReport(pages=2, ocr_needed_pages=[1, 2])
            return ParseOutcome(document_version_id=inp.ref_id, parsed=False, report=waiting)
        return ParseOutcome(document_version_id=inp.ref_id, parsed=True, report=ParseReport())

    @activity.defn(name="mark_parse_failed")
    async def mark_parse_failed(failure: ParseFailure) -> ParseOutcome:
        fakes.gave_up.append(failure.code)
        return ParseOutcome(document_version_id=failure.ref.ref_id, parsed=False, code=failure.code)

    @activity.defn(name="ocr_document")
    async def ocr_document(inp: StageRef) -> ParseOutcome:
        fakes.ocr_calls += 1
        fakes.ocr_times.append(activity.info().started_time)
        if fakes.ocr_calls <= fakes.ocr_hang_first:
            await asyncio.sleep(30)  # a worker that died mid-OCR: no heartbeat arrives
        if fakes.ocr_calls <= fakes.ocr_fail_first:
            raise fakes.ocr_error
        if fakes.ocr == "failed":
            return ParseOutcome(document_version_id=inp.ref_id, parsed=False, code=ErrorCode.EMPTY)
        done = ParseReport(pages=2, ocr_needed_pages=[1, 2], ocr_pages=[1, 2])
        return ParseOutcome(document_version_id=inp.ref_id, parsed=True, report=done)

    @activity.defn(name="mark_ocr_failed")
    async def mark_ocr_failed(failure: ParseFailure) -> ParseOutcome:
        fakes.ocr_gave_up.append(failure.code)
        return ParseOutcome(document_version_id=failure.ref.ref_id, parsed=False, code=failure.code)

    @activity.defn(name="register_attachments")
    async def register_attachments(inp: StageRef) -> IdsResult:
        fakes.register_calls.append(inp.ref_id)
        if len(fakes.register_calls) > 1:  # a child e-mail is not asked again in these tests
            return IdsResult(ids=[])
        return IdsResult(ids=fakes.child_ids)

    @activity.defn(name="finish_attachments")
    async def finish_attachments(inp: StageRef) -> IdsResult:
        fakes.finish_calls += 1
        return IdsResult(ids=fakes.child_ids)

    return [
        register_attachments,
        finish_attachments,
        scan_clamav,
        mark_scan_failed,
        detect_type,
        parse_document,
        mark_parse_failed,
        ocr_document,
        mark_ocr_failed,
    ]


@asynccontextmanager
async def running(fakes: Fakes, *, sandbox: bool = True) -> AsyncIterator[WorkflowEnvironment]:
    env = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    async with env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[DocumentIngestion],
            activities=activities(fakes),
            workflow_runner=SandboxedWorkflowRunner() if sandbox else UnsandboxedWorkflowRunner(),
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


def execute(fakes: Fakes, inp: StageRef, *, sandbox: bool = True) -> list[uuid.UUID]:
    """Run one DocumentIngestion on a fresh time-skipping server; return the ids it hands on."""

    async def scenario() -> list[uuid.UUID]:
        async with running(fakes, sandbox=sandbox) as env:
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


# --- E4.5: the parse step after detection ---------------------------------------------------------


def test_e4_5_a_detected_version_is_parsed_and_handed_on() -> None:
    fakes = Fakes()
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert (fakes.detect_calls, fakes.parse_calls, fakes.gave_up) == (1, 1, [])


def test_e4_5_a_file_the_loader_cannot_read_ends_the_execution_with_nothing_handed_on() -> None:
    fakes = Fakes(parse="failed")
    assert execute(fakes, stage_ref()) == []
    assert fakes.parse_calls == 1 and fakes.gave_up == []  # its own verdict: no retry, no marking


def test_e4_5_a_type_with_no_loader_yet_is_handed_on_as_before() -> None:
    fakes = Fakes(parse="no_loader")
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id] and fakes.gave_up == []


@pytest.mark.parametrize("verdict", [ScanStatus.INFECTED, ScanStatus.SCAN_FAILED])
def test_e4_5_nothing_is_parsed_after_a_scan_that_was_not_clean(verdict: ScanStatus) -> None:
    fakes = Fakes(verdict=verdict)
    assert execute(fakes, stage_ref()) == []
    assert fakes.parse_calls == 0


def test_e4_5_nothing_is_parsed_after_a_rejected_type() -> None:
    fakes = Fakes(detect="reject")
    assert execute(fakes, stage_ref()) == []
    assert fakes.parse_calls == 0


@pytest.mark.parametrize("failures", [1, 2])
def test_e4_5_a_transient_parse_error_is_retried_with_backoff(failures: int) -> None:
    fakes = Fakes(parse_fail_first=failures)
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert fakes.parse_calls == failures + 1 and fakes.gave_up == []
    waits = [b - a for a, b in zip(fakes.parse_times, fakes.parse_times[1:], strict=False)]
    assert waits[0] >= timedelta(seconds=10)
    if failures == 2:
        assert waits[1] >= timedelta(seconds=20)  # 10 s, doubling


def test_e4_5_three_failed_attempts_mark_the_version_parse_failed_and_the_run_goes_on() -> None:
    fakes = Fakes(parse_fail_first=99)
    assert execute(fakes, stage_ref()) == []  # ends normally: the other documents go on
    assert fakes.parse_calls == 3 and fakes.gave_up == [ErrorCode.PARSE_ERROR]


@pytest.mark.parametrize("kind", ["NotFound", "NotParseable", "NotScanned"])
def test_e4_5_a_permanent_parse_problem_is_not_retried_and_not_marked(kind: str) -> None:
    error = ApplicationError("no", type=kind, non_retryable=True)
    fakes = Fakes(parse_fail_first=99, parse_error=error)
    with pytest.raises(WorkflowFailureError):
        execute(fakes, stage_ref())
    assert fakes.parse_calls == 1 and fakes.gave_up == []


def test_e4_5_t06_a_worker_that_dies_mid_parse_is_retried_after_the_heartbeat_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_ingestion, "PARSE_HEARTBEAT_SECONDS", 1)
    fakes = Fakes(parse_hang_first=1)
    inp = stage_ref()
    assert execute(fakes, inp, sandbox=False) == [inp.ref_id]
    assert fakes.parse_calls == 2 and fakes.gave_up == []  # retried, then parsed once


def test_e4_5_a_parse_that_never_beats_its_heart_ends_as_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_ingestion, "PARSE_HEARTBEAT_SECONDS", 1)
    fakes = Fakes(parse_hang_first=99)
    assert execute(fakes, stage_ref(), sandbox=False) == []
    assert fakes.parse_calls == 3 and fakes.gave_up == [ErrorCode.TIMEOUT]


def test_e4_5_the_parse_limits_are_the_ones_in_the_contract() -> None:
    from sbam_contracts import documents

    assert document_ingestion.PARSE_RETRY.maximum_attempts == 3
    assert document_ingestion.PARSE_RETRY.initial_interval == timedelta(seconds=10)
    assert document_ingestion.PARSE_RETRY.backoff_coefficient == 2.0
    assert documents.PARSE_TIMEOUT_SECONDS == 600  # the activity's start-to-close timeout
    assert documents.PARSE_HEARTBEAT_SECONDS == 60  # and its heartbeat timeout


def test_e4_5_an_execution_started_before_parsing_existed_still_replays() -> None:
    """Determinism guard (TD §13.4): `before_e4_5` was recorded with the workflow as it was when
    detection was the last step; the `e4-5-parse` patch keeps it replayable."""
    path = Path(__file__).parent / "histories" / "document_ingestion_before_e4_5.json"
    history = WorkflowHistory.from_json(
        f"ingest-{uuid.UUID(int=1)}", json.loads(path.read_text("utf-8"))
    )
    replayer = Replayer(workflows=[DocumentIngestion], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


# --- E4.8: OCR after a parse that left pages for the vision model ---------------------------------


def test_e4_8_pages_waiting_for_the_model_are_read_and_the_version_is_handed_on() -> None:
    fakes = Fakes(parse="ocr")
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert (fakes.parse_calls, fakes.ocr_calls, fakes.ocr_gave_up) == (1, 1, [])


@pytest.mark.parametrize("parse", ["parsed", "failed", "no_loader"])
def test_e4_8_ocr_only_runs_when_pages_are_waiting(parse: str) -> None:
    fakes = Fakes(parse=parse)
    execute(fakes, stage_ref())
    assert fakes.ocr_calls == 0  # a parsed, failed or loader-less version has nothing to read


def test_e4_8_a_document_the_model_cannot_read_ends_the_execution_with_nothing_handed_on() -> None:
    fakes = Fakes(parse="ocr", ocr="failed")
    assert execute(fakes, stage_ref()) == []
    assert fakes.ocr_calls == 1 and fakes.ocr_gave_up == []  # its own verdict: no retry


@pytest.mark.parametrize("verdict", [ScanStatus.INFECTED, ScanStatus.SCAN_FAILED])
def test_e4_8_nothing_is_read_after_a_scan_that_was_not_clean(verdict: ScanStatus) -> None:
    fakes = Fakes(parse="ocr", verdict=verdict)
    assert execute(fakes, stage_ref()) == []
    assert (fakes.parse_calls, fakes.ocr_calls) == (0, 0)


@pytest.mark.parametrize("failures", [1, 2])
def test_e4_8_a_provider_error_is_retried_with_backoff(failures: int) -> None:
    fakes = Fakes(parse="ocr", ocr_fail_first=failures)
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert fakes.ocr_calls == failures + 1 and fakes.ocr_gave_up == []
    waits = [b - a for a, b in zip(fakes.ocr_times, fakes.ocr_times[1:], strict=False)]
    assert waits[0] >= timedelta(seconds=10)
    if failures == 2:
        assert waits[1] >= timedelta(seconds=20)


def test_e4_8_t05_three_failed_attempts_make_the_pages_unreadable_and_the_run_goes_on() -> None:
    fakes = Fakes(parse="ocr", ocr_fail_first=99)
    assert execute(fakes, stage_ref()) == []  # the version is PARSE_FAILED or PARSED, never stuck
    assert fakes.ocr_calls == 3 and fakes.ocr_gave_up == [ErrorCode.OCR_UNAVAILABLE]


@pytest.mark.parametrize("kind", ["NotFound", "NotParseable", "NotScanned"])
def test_e4_8_a_permanent_problem_is_not_retried_and_not_marked(kind: str) -> None:
    error = ApplicationError("no", type=kind, non_retryable=True)
    fakes = Fakes(parse="ocr", ocr_fail_first=99, ocr_error=error)
    with pytest.raises(WorkflowFailureError):
        execute(fakes, stage_ref())
    assert fakes.ocr_calls == 1 and fakes.ocr_gave_up == []


def test_e4_8_an_ocr_that_never_beats_its_heart_ends_as_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_ingestion, "OCR_HEARTBEAT_SECONDS", 1)
    fakes = Fakes(parse="ocr", ocr_hang_first=99)
    assert execute(fakes, stage_ref(), sandbox=False) == []
    assert fakes.ocr_calls == 3 and fakes.ocr_gave_up == [ErrorCode.TIMEOUT]


def test_e4_8_a_worker_that_dies_mid_ocr_is_retried_after_the_heartbeat_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_ingestion, "OCR_HEARTBEAT_SECONDS", 1)
    fakes = Fakes(parse="ocr", ocr_hang_first=1)
    inp = stage_ref()
    assert execute(fakes, inp, sandbox=False) == [inp.ref_id]
    assert fakes.ocr_calls == 2 and fakes.ocr_gave_up == []


def test_e4_8_the_ocr_limits_are_the_ones_in_the_contract() -> None:
    from sbam_contracts import documents

    assert document_ingestion.OCR_RETRY.maximum_attempts == documents.OCR_PAGE_ATTEMPTS == 3
    assert document_ingestion.OCR_RETRY.initial_interval == timedelta(seconds=10)
    assert document_ingestion.OCR_RETRY.backoff_coefficient == 2.0
    assert documents.OCR_TIMEOUT_SECONDS == 1800  # up to 200 pages, one model call each
    assert documents.OCR_HEARTBEAT_SECONDS == 60


def test_e4_8_an_execution_started_before_ocr_existed_still_replays() -> None:
    """Determinism guard (TD §13.4): `before_e4_8` was recorded with the workflow as it was when
    parsing was the last step; the `e4-8-ocr` patch is only reached when pages wait for OCR."""
    path = Path(__file__).parent / "histories" / "document_ingestion_before_e4_8.json"
    history = WorkflowHistory.from_json(
        f"ingest-{uuid.UUID(int=1)}", json.loads(path.read_text("utf-8"))
    )
    replayer = Replayer(workflows=[DocumentIngestion], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


# --- E4.6: attachments of a parsed e-mail ---------------------------------------------------------


def test_e4_6_every_attachment_gets_its_own_ingestion_and_the_parent_is_handed_on() -> None:
    fakes = Fakes(child_ids=[uuid7(), uuid7()])
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert fakes.register_calls[0] == inp.ref_id and fakes.finish_calls == 1
    assert len(fakes.scan_times) == 3  # the e-mail and its two attachments are each scanned
    assert fakes.detect_calls == 3 and fakes.parse_calls == 3


def test_e4_6_a_child_that_fails_does_not_fail_the_parent() -> None:
    first, second = uuid7(), uuid7()
    fakes = Fakes(child_ids=[first, second], failing_children={first})
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]  # the first child's workflow failed: still handed on
    assert fakes.finish_calls == 1  # and what became of the children is still written
    assert fakes.parse_calls == 2  # the e-mail and the second child; the first never got that far


def test_e4_6_an_email_without_attachments_runs_no_child_and_no_finish() -> None:
    fakes = Fakes()
    inp = stage_ref()
    assert execute(fakes, inp) == [inp.ref_id]
    assert len(fakes.register_calls) == 1 and fakes.finish_calls == 0
    assert len(fakes.scan_times) == 1


@pytest.mark.parametrize("parse", ["failed"])
def test_e4_6_attachments_are_only_looked_for_after_a_successful_parse(parse: str) -> None:
    fakes = Fakes(parse=parse, child_ids=[uuid7(), uuid7()])
    assert execute(fakes, stage_ref()) == []
    assert fakes.register_calls == []


def test_e4_6_an_execution_started_before_attachments_existed_still_replays() -> None:
    """The same recorded history as `before_e4_8` (parse is the last step): the `e4-6-attachments`
    patch is not in it, so the replay skips the new step."""
    path = Path(__file__).parent / "histories" / "document_ingestion_before_e4_8.json"
    history = WorkflowHistory.from_json(
        f"ingest-{uuid.UUID(int=1)}", json.loads(path.read_text("utf-8"))
    )
    replayer = Replayer(workflows=[DocumentIngestion], data_converter=pydantic_data_converter)
    asyncio.run(replayer.replay_workflow(history))


@pytest.mark.skipif(not os.environ.get("RECORD_HISTORY"), reason="RECORD_HISTORY=1 to re-record")
def test_record_document_ingestion_with_attachments_history() -> None:  # pragma: no cover
    """Maintenance helper (`make record-histories`): an e-mail with one attachment, the path of
    the `e4-6-attachments` patch. `test_replay_histories.py` replays it."""
    fakes = Fakes(child_ids=[uuid.UUID(int=2)])
    inp = stage_ref()

    async def scenario() -> dict[str, Any]:
        async with running(fakes) as env:
            handle = await env.client.start_workflow(
                DocumentIngestion.run, inp, id=f"ingest-{inp.ref_id}", task_queue=QUEUE
            )
            await handle.result()
            data: dict[str, Any] = json.loads((await handle.fetch_history()).to_json())
            return data

    out = Path(__file__).parent / "histories" / "document_ingestion_with_attachments.json"
    out.write_text(json.dumps(asyncio.run(scenario()), indent=1) + "\n", encoding="utf-8")
