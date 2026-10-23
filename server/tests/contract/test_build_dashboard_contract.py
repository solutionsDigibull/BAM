"""E12.6 contract: the Build & Test dashboard's shapes (#121), pending the endpoints (#117 / #119).

The finding shape must stay the one the build worker already writes (`SyntaxFinding.as_dict`), so
the dashboard can show what the gates persist without a translation layer.
"""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.build_dashboard import (
    BuildRow,
    BuildsResponse,
    Finding,
    GateName,
    GateResult,
    GatesResponse,
    GateStatus,
)
from sbam_workers.build.codegen.bundle import SyntaxFinding


def build_row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sub_skill_id": str(uuid.uuid4()),
        "skill_id": "MS01.1_intake",
        "skill_name": "Intake",
        "state": "BUILDING",
        "stage": "ast_gate",
        "attempt": 1,
        "gates": {"ISATVON_CONTRACT": "PASSED", "AST_SYNTAX": "RUNNING"},
    }
    return base | over


def test_e12_6_builds_response_round_trips() -> None:
    body = {
        "workflow_id": str(uuid.uuid4()),
        "workflow_state": "BUILDING",
        "active": True,
        "sub_skills": [build_row(), build_row(state="NEEDS_FDE_REVIEW", stage="wait_retry")],
    }
    parsed = BuildsResponse.model_validate(body)
    assert parsed.sub_skills[0].gates[GateName.AST_SYNTAX] is GateStatus.RUNNING
    assert BuildsResponse.model_validate_json(parsed.model_dump_json()) == parsed


def test_e12_6_a_gate_column_never_comes_from_an_unknown_key() -> None:
    with pytest.raises(ValidationError):
        BuildRow.model_validate(build_row(gates={"HACKED": "PASSED"}))
    with pytest.raises(ValidationError):
        BuildRow.model_validate(build_row(gates={"RUFF": "MAYBE"}))


@pytest.mark.parametrize("attempt", [-1, 3])
def test_e12_6_attempt_is_0_to_2(attempt: int) -> None:
    with pytest.raises(ValidationError):
        BuildRow.model_validate(build_row(attempt=attempt))
    with pytest.raises(ValidationError):
        GatesResponse(sub_skill_id=uuid.uuid4(), attempt=attempt, gates=[])


def test_e12_6_the_workers_syntax_finding_is_a_valid_finding() -> None:
    raw = SyntaxFinding(file="skill/handoff.py", message="invalid syntax", line=12, col=4).as_dict()
    finding = Finding.model_validate(raw)
    assert finding.gate is GateName.AST_SYNTAX
    assert (finding.file, finding.line, finding.col) == ("skill/handoff.py", 12, 4)


def test_e12_6_gates_response_with_findings() -> None:
    result = GateResult(
        gate=GateName.AST_HANDOFF,
        status=GateStatus.FAILED,
        findings=[Finding(gate=GateName.AST_HANDOFF, code="AST_HANDOFF", message="<b>x</b>")],
    )
    resp = GatesResponse(sub_skill_id=uuid.uuid4(), attempt=2, gates=[result])
    assert resp.gates[0].findings[0].message == "<b>x</b>"  # kept as text; the UI shows it as text
    with pytest.raises(ValidationError):
        Finding.model_validate({"gate": "RUFF", "code": "E501", "message": "x", "extra": 1})
