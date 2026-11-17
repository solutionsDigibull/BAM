"""#802: a document's relevance to the project's goal is stored on its version and returned by
`GET /projects/{id}/documents`; None until it is scored. The answer the model may give is strict."""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sbam_api.ingest.models import DocumentVersion
from sbam_contracts.documents import DocumentRelevance, RelevanceAnswer, RelevanceLevel
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy import select
from test_uploads import Harness

GOAL_SHA = hashlib.sha256(b"Build an AI agent that automates quoting").hexdigest()


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def documents(h: Harness) -> list[dict[str, object]]:
    res = h.call("GET", f"/projects/{h.project}/documents", h.fde())
    assert res.status_code == 200, res.text
    items: list[dict[str, object]] = res.json()["items"]
    return items


def test_802_t01_a_document_has_no_relevance_until_scored(h: Harness) -> None:
    h.seed_versions(1, S.PARSED.value)
    [doc] = documents(h)
    assert doc["relevance"] is None


def test_802_t02_a_scored_document_returns_its_relevance(h: Harness) -> None:
    h.seed_versions(1, S.PARSED.value)
    stored = DocumentRelevance(
        level=RelevanceLevel.HIGH,
        score=88,
        reason="Lists the harness parts the quoting agent must price.",
        goal_sha256=GOAL_SHA,
        scored_at=datetime(2026, 11, 17, 9, 0, tzinfo=UTC),
    )
    with h.sessions() as s:
        version = s.scalars(select(DocumentVersion)).one()
        version.relevance = stored.model_dump(mode="json")
        s.commit()
    [doc] = documents(h)
    assert doc["relevance"] == stored.model_dump(mode="json")


def test_802_t03_the_answer_is_strict() -> None:
    ok = {"level": "MEDIUM", "score": 50, "reason": "Shows the assembly steps."}
    assert RelevanceAnswer.model_validate(ok).level is RelevanceLevel.MEDIUM
    for bad in (
        {**ok, "status": "approved"},  # a field the model may not set
        {**ok, "score": 101},
        {**ok, "reason": ""},
        {**ok, "reason": "x" * 201},
        {**ok, "level": "CRITICAL"},
    ):
        with pytest.raises(ValidationError):
            RelevanceAnswer.model_validate(bad)


@pytest.mark.parametrize(
    ("level", "inside", "outside"),
    [("HIGH", 67, 66), ("MEDIUM", 34, 67), ("LOW", 33, 34)],
)
def test_802_t04_the_score_must_fit_its_level(level: str, inside: int, outside: int) -> None:
    reason = "A reason."
    assert RelevanceAnswer(level=RelevanceLevel(level), score=inside, reason=reason)
    with pytest.raises(ValidationError):
        RelevanceAnswer(level=RelevanceLevel(level), score=outside, reason=reason)
