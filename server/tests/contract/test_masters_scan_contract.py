"""Contract tests for the masters scan run (#609, N-02b option (a)): ids, shapes, the 202 body."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sbam_contracts.api import ErrorCode, ProblemDetail
from sbam_contracts.masters_scan import (
    MASTERS_SCAN_IMPORT,
    MastersImportAccepted,
    MastersScanInput,
    MastersScanKind,
    MastersScanState,
    MastersScanStatus,
    masters_scan_workflow_id,
)
from sbam_contracts.runs import WORKFLOW_ID, Run, RunState, SubjectType

NOW = datetime(2026, 11, 20, tzinfo=UTC)


def _run(scan_id: uuid.UUID) -> Run:
    return Run(
        id=uuid.uuid4(),
        temporal_workflow_id=masters_scan_workflow_id(scan_id),
        temporal_run_id="r1",
        type=MASTERS_SCAN_IMPORT,
        subject_type=SubjectType.PROJECT,
        subject_id=uuid.UUID(int=0),
        state=RunState.RUNNING,
        current_stage="queued",
        started_at=NOW,
        updated_at=NOW,
    )


def test_n02b_c01_workflow_id_is_a_valid_run_id() -> None:
    scan_id = uuid.uuid4()
    assert WORKFLOW_ID.fullmatch(masters_scan_workflow_id(scan_id))
    assert not WORKFLOW_ID.fullmatch("masters-scan-not-a-uuid")


def test_n02b_c02_accepted_carries_the_run_and_the_status_id() -> None:
    scan_id = uuid.uuid4()
    body = MastersImportAccepted(
        scan_id=scan_id,
        kind=MastersScanKind.IMPORT,
        state=MastersScanState.QUEUED,
        run=_run(scan_id),
    )
    assert MastersImportAccepted.model_validate_json(body.model_dump_json()) == body


def test_n02b_c03_a_failed_status_holds_a_problem_detail() -> None:
    scan_id = uuid.uuid4()
    problem = ProblemDetail(
        title="The workbook contains malware",
        status=422,
        code=ErrorCode.MALWARE,
        correlation_id="abc-123",
    )
    status = MastersScanStatus(
        scan_id=scan_id,
        kind=MastersScanKind.DOCUMENT,
        state=MastersScanState.FAILED,
        error=problem,
        created_at=NOW,
        updated_at=NOW,
    )
    assert status.error is not None and status.error.code is ErrorCode.MALWARE


def test_n02b_c04_the_input_holds_ids_and_keys_only() -> None:
    with pytest.raises(ValidationError):
        MastersScanInput.model_validate({"bytes": "AAAA"})
