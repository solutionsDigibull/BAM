"""E0.7 contract tests: ProblemDetail examples, committed JSON Schema, error-code registry."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sbam_contracts.api import PROBLEM_JSON, ErrorCode, Page, PageParams, ProblemDetail

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
EXAMPLES = sorted((CONTRACTS / "examples/problems").glob("*.json"))
REQUIRED_STATUSES = {400, 401, 403, 404, 409, 412, 413, 415, 422, 423, 428, 429, 503}


def test_e0_7_t01_every_status_has_an_example() -> None:
    assert {int(p.stem) for p in EXAMPLES} == REQUIRED_STATUSES
    assert PROBLEM_JSON == "application/problem+json"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_e0_7_t01_example_validates(path: Path) -> None:
    problem = ProblemDetail.model_validate_json(path.read_text(encoding="utf-8"))
    assert problem.status == int(path.stem)
    assert problem.correlation_id


def test_e0_7_t01_committed_schema_matches_model() -> None:
    committed = json.loads((CONTRACTS / "schemas/problem_detail.schema.json").read_text("utf-8"))
    assert committed == ProblemDetail.model_json_schema(), "regenerate the schema from the model"


def test_e0_7_error_codes_are_unique_and_self_named() -> None:
    values = [c.value for c in ErrorCode]
    assert len(values) == len(set(values))
    assert all(c.name == c.value for c in ErrorCode)


def test_e0_7_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        ProblemDetail.model_validate(
            {
                "title": "x",
                "status": 500,
                "code": "INTERNAL_ERROR",
                "correlation_id": "c",
                "trace": "…",
            }
        )


@pytest.mark.parametrize(
    "detail",
    [
        'Traceback (most recent call last):\n  File "/app/x.py", line 3',
        "SELECT * FROM sbam.projects WHERE id = 1",
        "psycopg.errors.UniqueViolation",
        "could not open /var/lib/sbam/storage/abc",
        r"could not open D:\SBAM\storage\abc",
        "Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
        "key sk-or-v1-0123456789abcdef0123",
    ],
)
def test_e0_7_detail_never_exposes_internals(detail: str) -> None:
    with pytest.raises(ValidationError):
        ProblemDetail(
            title="x", status=500, code=ErrorCode.INTERNAL_ERROR, detail=detail, correlation_id="c"
        )


def test_e0_7_plain_detail_allowed() -> None:
    problem = ProblemDetail(
        title="Conflict",
        status=409,
        code=ErrorCode.PIPELINE_RUNNING,
        detail="Delete the draft from the recycle bin first.",
        correlation_id="c",
    )
    assert problem.detail


def test_e0_7_page_contract() -> None:
    page = Page[int].model_validate({"items": [1, 2], "next_cursor": None})
    assert page.items == [1, 2]
    assert PageParams().limit == 25
    with pytest.raises(ValidationError):
        PageParams(limit=101)
    with pytest.raises(ValidationError):
        Page[int].model_validate({"items": ["x"]})
