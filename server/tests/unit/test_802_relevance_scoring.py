"""#802: `score_version` asks the model how useful a parsed document is for the project's goal and
stores the answer on the version. Nothing is asked without a goal or before the document is parsed;
a version already scored against the same goal is not asked again; a failed answer stores nothing.
"""

import asyncio
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from knowledge_db import DERIVED, World, add_document
from sbam_api.ingest.models import DocumentVersion
from sbam_api.projects.models import Project
from sbam_contracts.documents import RelevanceAnswer, RelevanceLevel
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest.relevance import (
    ALIAS,
    MAX_DOCUMENT_CHARS,
    PROMPT,
    RelevanceDeps,
    ScoreRun,
    goal_sha256,
    parsed_ids,
    score_version,
)
from sbam_workers.knowledge.derivatives import DOC_MD
from sbam_workers.llm.client import LlmRequestRejected, LlmResult
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.store import LlmScope

GOAL = "Build an AI agent that automates quoting for sales managers at SMB manufacturers in India"
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
RUN = ScoreRun(workflow_id="ingest-x", activity_id="7", run_id="r1")


class FakeScorer:
    """Answers `answer`, or raises `error`; records what it was asked."""

    def __init__(self, answer: RelevanceAnswer | None = None, error: Exception | None = None):
        self.answer = answer or RelevanceAnswer(
            level=RelevanceLevel.HIGH, score=88, reason="Lists the parts the agent must price."
        )
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        alias: str,
        prompt_id: str,
        output_type: type[RelevanceAnswer],
        context: Mapping[str, str],
        scope: LlmScope,
        **kwargs: Any,
    ) -> LlmResult[RelevanceAnswer]:
        self.calls.append({"alias": alias, "prompt": prompt_id, "context": dict(context), **kwargs})
        if self.error is not None:
            raise self.error
        return LlmResult(output=self.answer, llm_call_id=uuid.uuid4(), trace_id="t", attempts=1)


@pytest.fixture
def w(tmp_path: Path) -> World:
    return World(tmp_path)


TABLE = "| Part | Qty |\n|---|---|\n| HX-1 | 42 |"


def parsed(w: World, text: str = TABLE, **fields: Any) -> uuid.UUID:
    """A version (PARSED, CLEAN by default) with its doc.md linked as the parser leaves it."""
    version_id = add_document(w.sessions, w.storage, w.project, text, **fields)
    with w.sessions() as s:
        version = s.get(DocumentVersion, version_id)
        assert version is not None
        md = DERIVED.get(version_id, {}).get(DOC_MD)
        version.derived_md_key = md.storage_key if md is not None else None
        s.commit()
    return version_id


def set_goal(w: World, goal: str | None) -> None:
    with w.sessions() as s:
        project = s.get(Project, w.project)
        assert project is not None
        project.goal = goal
        s.commit()


def ref(w: World, version_id: uuid.UUID) -> StageRef:
    return StageRef(**w.run_input().model_dump(), ref_id=version_id)


def deps(w: World, scorer: FakeScorer) -> RelevanceDeps:
    return RelevanceDeps(sessions=w.sessions, storage=w.storage, scorer=scorer, clock=lambda: NOW)


def stored(w: World, version_id: uuid.UUID) -> dict[str, Any] | None:
    with w.sessions() as s:
        version = s.get(DocumentVersion, version_id)
        assert version is not None
        return dict(version.relevance) if version.relevance else None


def score(w: World, version_id: uuid.UUID, scorer: FakeScorer) -> bool:
    return asyncio.run(score_version(deps(w, scorer), ref(w, version_id), RUN))


def test_802_t10_a_parsed_document_is_scored_against_the_goal(w: World) -> None:
    set_goal(w, GOAL)
    version_id = parsed(w)
    scorer = FakeScorer()
    assert score(w, version_id, scorer) is True
    [call] = scorer.calls
    assert (call["alias"], call["prompt"]) == (ALIAS, PROMPT)
    assert call["context"]["goal"] == GOAL and "HX-1" in call["context"]["document"]
    assert call["subject"] == {"document_version_id": str(version_id)}
    assert stored(w, version_id) == {
        "level": "HIGH",
        "score": 88,
        "reason": "Lists the parts the agent must price.",
        "goal_sha256": goal_sha256(GOAL),
        "scored_at": NOW.isoformat().replace("+00:00", "Z"),
    }


def test_802_t11_no_goal_asks_nothing(w: World) -> None:
    version_id = parsed(w)
    scorer = FakeScorer()
    assert score(w, version_id, scorer) is False
    assert scorer.calls == [] and stored(w, version_id) is None


@pytest.mark.parametrize(
    "fields", [{"state": "PARSING"}, {"state": "PARSE_FAILED"}, {"scan": "INFECTED"}]
)
def test_802_t12_only_parsed_clean_documents_are_scored(w: World, fields: dict[str, Any]) -> None:
    set_goal(w, GOAL)
    version_id = parsed(w, **fields)
    scorer = FakeScorer()
    assert score(w, version_id, scorer) is False and scorer.calls == []


def test_802_t13_the_same_goal_is_not_asked_twice_a_new_goal_is(w: World) -> None:
    set_goal(w, GOAL)
    version_id = parsed(w)
    scorer = FakeScorer()
    assert score(w, version_id, scorer) and score(w, version_id, scorer)
    assert len(scorer.calls) == 1  # already scored against this goal
    set_goal(w, "Build an AI agent that answers RFQs for procurement teams in Europe")
    assert score(w, version_id, scorer) and len(scorer.calls) == 2
    assert stored(w, version_id)["goal_sha256"] != goal_sha256(GOAL)  # type: ignore[index]


@pytest.mark.parametrize(
    "error",
    [SchemaInvalidAfterRetries(attempts=()), LlmRequestRejected("refused")],
    ids=["schema", "rejected"],
)
def test_802_t14_a_failed_answer_stores_nothing(w: World, error: Exception) -> None:
    set_goal(w, GOAL)
    version_id = parsed(w)
    assert score(w, version_id, FakeScorer(error=error)) is False
    assert stored(w, version_id) is None


def test_802_t15_only_the_start_of_a_long_document_is_sent(w: World) -> None:
    set_goal(w, GOAL)
    version_id = parsed(w, text="x" * (MAX_DOCUMENT_CHARS + 5000))
    scorer = FakeScorer()
    score(w, version_id, scorer)
    assert len(scorer.calls[0]["context"]["document"]) == MAX_DOCUMENT_CHARS


def test_802_t16_parsed_ids_lists_only_parsed_clean_versions(w: World) -> None:
    good = parsed(w)
    parsed(w, state="PARSING")
    parsed(w, scan="INFECTED")
    assert parsed_ids(deps(w, FakeScorer()), w.run_input()) == [good]


def test_802_t17_the_prompt_judges_any_goal_not_one_industry() -> None:
    """The goal is whatever the client wrote; the prompt must not steer towards a domain."""
    from sbam_workers.llm.registry import PromptRegistry

    text = PromptRegistry().get(PROMPT).text.lower()
    assert "do not assume an industry" in text
    for word in ("manufactur", "quoting", "rfq", "bom"):
        assert word not in text
