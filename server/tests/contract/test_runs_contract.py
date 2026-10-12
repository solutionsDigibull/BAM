"""E1.7 contract tests: task queues, workflow ids, run object and payload limit (TD §13.1)."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.api import ErrorCode, ProblemDetail
from sbam_contracts.runs import (
    MAX_PAYLOAD_BYTES,
    TERMINAL_STATES,
    WORKFLOW_ID,
    MirrorUpdate,
    PingResult,
    Run,
    RunAccepted,
    RunInput,
    RunState,
    TaskQueue,
    build_workflow_id,
    ingest_workflow_id,
    ping_workflow_id,
    pipeline_workflow_id,
)

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
PROJECT = UUID("0192f0a0-0000-7000-8000-000000000001")
RUN = {
    "id": "0192f0a0-0000-7000-8000-0000000000aa",
    "temporal_workflow_id": f"pipeline-{PROJECT}",
    "temporal_run_id": "4f8c0b6e-1a7d-4c0f-9a3e-2b1d9c7e5f10",
    "type": "ProjectPipeline",
    "subject_type": "project",
    "subject_id": str(PROJECT),
    "state": "RUNNING",
    "current_stage": "ingest",
    "started_at": "2026-10-12T10:00:00Z",
    "updated_at": "2026-10-12T10:00:05Z",
}


def test_e1_7_six_task_queues() -> None:
    assert [q.value for q in TaskQueue] == [
        "sbam-ingest",
        "sbam-knowledge",
        "sbam-llm",
        "sbam-build",
        "sbam-test",
        "sbam-certify",
    ]


def test_e1_7_workflow_ids_follow_td_13_1() -> None:
    ids = [
        pipeline_workflow_id(PROJECT),
        ingest_workflow_id(PROJECT),
        build_workflow_id(PROJECT, 3),
        *(ping_workflow_id(q, PROJECT) for q in TaskQueue),
    ]
    assert all(WORKFLOW_ID.match(i) for i in ids)
    assert ids[0] == "pipeline-0192f0a0-0000-7000-8000-000000000001"
    with pytest.raises(ValueError, match="build_no"):
        build_workflow_id(PROJECT, 0)


@pytest.mark.parametrize(
    "bad", ["pipeline-x", f"build-{PROJECT}-0", f"ping-sbam-foo-{PROJECT}", f"Pipeline-{PROJECT}"]
)
def test_e1_7_bad_workflow_ids_rejected(bad: str) -> None:
    assert not WORKFLOW_ID.match(bad)
    with pytest.raises(ValidationError):
        Run.model_validate({**RUN, "temporal_workflow_id": bad})


def test_e1_7_run_round_trips_from_json() -> None:
    run = Run.model_validate_json(json.dumps(RUN))
    assert run.state is RunState.RUNNING
    accepted = RunAccepted(run=run)
    assert RunAccepted.model_validate_json(accepted.model_dump_json()) == accepted


def test_e1_7_terminal_states() -> None:
    assert RunState.RUNNING not in TERMINAL_STATES
    assert {RunState.COMPLETED, RunState.FAILED} <= TERMINAL_STATES


def test_e1_7_run_input_is_ids_only() -> None:
    ids = {"tenant_id": str(PROJECT), "project_id": str(PROJECT), "run_id": str(PROJECT)}
    RunInput.model_validate({**ids, "correlation_id": "abc-123"})
    with pytest.raises(ValidationError):
        RunInput.model_validate({**ids, "correlation_id": "abc-123", "document_text": "…"})
    with pytest.raises(ValidationError):
        RunInput.model_validate({**ids, "correlation_id": "bad id with spaces"})
    assert MAX_PAYLOAD_BYTES == 65536


@pytest.mark.parametrize(("path", "model"), [("runs/run", Run), ("runs/run_accepted", RunAccepted)])
def test_e1_7_committed_schemas_match(path: str, model: type[Run] | type[RunAccepted]) -> None:
    committed = json.loads((CONTRACTS / f"schemas/{path}.schema.json").read_text("utf-8"))
    assert committed == contract_schema(model, path), "regenerate with `make contracts`"


def test_e1_7_t03_503_example_uses_orchestrator_unavailable() -> None:
    example = (CONTRACTS / "examples/problems/503.json").read_text("utf-8")
    assert ProblemDetail.model_validate_json(example).code is ErrorCode.ORCHESTRATOR_UNAVAILABLE


def test_e1_7_activity_dtos_are_ids_only() -> None:
    update = MirrorUpdate(
        tenant_id=PROJECT,
        project_id=PROJECT,
        temporal_workflow_id=f"pipeline-{PROJECT}",
        temporal_run_id="r1",
        state=RunState.COMPLETED,
    )
    assert MirrorUpdate.model_validate_json(update.model_dump_json()) == update
    with pytest.raises(ValidationError):
        MirrorUpdate.model_validate({**update.model_dump(), "last_error": "x" * 501})
    result = PingResult.model_validate_json('{"task_queue": "sbam-llm", "attempt": 2}')
    assert result.task_queue is TaskQueue.LLM
