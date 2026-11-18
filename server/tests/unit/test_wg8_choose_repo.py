"""#756 (WG-8): the `choose_repo` stage with a fake scout and an in-memory store (no model, no DB).

- WG8-T01: one clear winner is used and recorded without asking.
- WG8-T02: a tie (and a weak match) records a question and decides nothing.
- WG8-T03: the FDE's answer is recorded as the decision (who, when) and drives the result.
- WG8-T04: "no repository" (and no candidate at all) falls back to generation.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pipeline_db import pipeline_sessions
from sbam_contracts.build import BuildStageInput
from sbam_contracts.repo_choice import (
    ChooseRepoInput,
    ChooseRepoResult,
    ChooseRepoStatus,
    RepoAnswerAction,
    RepoAnswerSignal,
    RepoQuestion,
    RepoQuestionKind,
)
from sbam_contracts.repo_decision import DecidedBy, RepoDecision, RepoOutcome, RepoSource
from sbam_workers.build.repo_choice import (
    CLEAR_MIN,
    MARGIN,
    RELATED_MIN,
    ChoiceInputs,
    ChooseRepo,
    SqlChoiceStore,
    judge,
)
from sbam_workers.build.repo_scout import ScoredRepo
from sbam_workers.llm.budget import BudgetExceeded
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_wg11_repo_scout import FakeScout, candidate

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
FDE = "fde-user-1"


def scored(name: str, score: float) -> ScoredRepo:
    return ScoredRepo(f"c-{name}", f"https://github.com/acme/{name}", score, ("fits",))


class MemoryStore:
    def __init__(self) -> None:
        self.asked: RepoQuestion | None = None
        self.decided: RepoDecision | None = None

    def question(self, inp: BuildStageInput) -> RepoQuestion | None:
        return self.asked

    def decision(self, inp: BuildStageInput) -> RepoDecision | None:
        return self.decided

    def put_question(self, inp: BuildStageInput, question: RepoQuestion) -> None:
        assert self.asked is None, "one question per build"
        self.asked = question

    def put_decision(self, inp: BuildStageInput, decision: RepoDecision) -> None:
        assert self.decided is None, "one decision per build"
        self.decided = decision


def build_input(**over: Any) -> ChooseRepoInput:
    values: dict[str, Any] = {
        "tenant_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "correlation_id": "corr-756",
        "workflow_id": uuid.uuid4(),
        "sub_skill_id": uuid.uuid4(),
        "build_no": 1,
    }
    return ChooseRepoInput(**{**values, **over})


class World:
    def __init__(
        self,
        scores: dict[str, float] | Exception,
        names: list[str],
        sub_skill: dict[str, Any] | None = None,
    ) -> None:
        self.catalog = [candidate(n) for n in names]
        reply = (
            scores
            if isinstance(scores, Exception)
            else {
                "scores": [
                    {"candidate_id": f"c-{n}", "score": s, "reasons": [f"{n} fits"]}
                    for n, s in scores.items()
                ]
            }
        )
        self.scout = FakeScout(reply)
        self.store = MemoryStore()
        self.sub_skill = sub_skill or {"skill_metadata": {"skill_id": "MS01.1"}}
        self.inp = build_input()

    async def _inputs(self, _: BuildStageInput) -> ChoiceInputs:
        return ChoiceInputs("sell widgets", self.sub_skill, self.catalog)

    def run(self, answer: RepoAnswerSignal | None = None) -> ChooseRepoResult:
        step = ChooseRepo(self._inputs, self.store, self.scout, clock=lambda: NOW)
        inp = self.inp.model_copy(update={"answer": answer})
        return asyncio.run(ActivityEnvironment().run(step.run, inp))


def answer(action: RepoAnswerAction, url: str | None = None) -> RepoAnswerSignal:
    return RepoAnswerSignal(action=action, url=url, idempotency_key="k-1", actor_id=FDE)


# --- the rules ----------------------------------------------------------------------------------


def test_wg8_the_bands_are_the_repo_scout_prompts() -> None:
    assert (CLEAR_MIN, RELATED_MIN) == (0.8, 0.4)  # "same job" and "related" in the prompt
    assert 0 < MARGIN < CLEAR_MIN


def test_wg8_judge_clear_winner_needs_the_band_and_a_lead() -> None:
    assert judge([scored("a", 0.9), scored("b", 0.5)]).winner == scored("a", 0.9)
    assert judge([scored("a", 0.9)]).winner is not None  # alone, and in the band
    close = judge([scored("a", 0.9), scored("b", 0.9 - MARGIN + 0.01)])
    assert close.winner is None and close.kind is RepoQuestionKind.CLOSE_CANDIDATES
    weak = judge([scored("a", 0.7), scored("b", 0.1)])
    assert weak.winner is None and weak.kind is RepoQuestionKind.WEAK_MATCH
    assert [r.candidate_id for r in weak.shortlist] == ["c-a"]  # only the related ones


def test_wg8_judge_a_weak_match_with_nothing_related_still_shows_the_best_few() -> None:
    rows = [scored(n, s) for n, s in [("a", 0.3), ("b", 0.2), ("c", 0.1), ("d", 0.0)]]
    verdict = judge(rows)
    assert verdict.kind is RepoQuestionKind.WEAK_MATCH
    assert [r.candidate_id for r in verdict.shortlist] == ["c-a", "c-b", "c-c"]


def test_wg8_judge_no_candidate_is_its_own_question() -> None:
    verdict = judge([])
    assert verdict.winner is None and verdict.kind is RepoQuestionKind.NO_CANDIDATE


def test_wg8_judge_the_short_list_is_capped() -> None:
    rows = [scored(f"r{i}", 0.85) for i in range(9)]
    assert len(judge(rows).shortlist) == 5


# --- WG8-T01 ------------------------------------------------------------------------------------


def test_wg8_t01_one_clear_winner_proceeds_without_asking() -> None:
    world = World({"a": 0.9, "b": 0.3}, ["a", "b"])
    result = world.run()
    assert result.status is ChooseRepoStatus.DECIDED and result.outcome is RepoOutcome.REPO
    assert result.url == "https://github.com/acme/a"
    assert world.store.asked is None  # nothing was asked
    decision = world.store.decided
    assert decision is not None and decision.decided_by is DecidedBy.AUTO
    assert decision.actor_id is None and decision.decided_at == NOW
    assert decision.chosen is not None and decision.chosen.url == result.url
    assert [c.source for c in decision.candidates] == [RepoSource.ADMIN_AGENTS]
    assert len(world.scout.calls) == 1


def test_wg8_t01_a_repository_named_in_the_isatvon_is_used_and_never_scored() -> None:
    named = {
        "isatvon_definition": {
            "t_tech_stack": {
                "source_repos": [{"url": "https://github.com/acme/named", "reason": "the API"}]
            }
        }
    }
    world = World({"a": 0.9}, ["a"], sub_skill=named)
    result = world.run()
    assert (
        result.status is ChooseRepoStatus.DECIDED and result.url == "https://github.com/acme/named"
    )
    assert world.scout.calls == []  # no model call, the catalogue was not read for scores
    decision = world.store.decided
    assert decision is not None and decision.candidates[0].source is RepoSource.ISATVON


# --- WG8-T02 ------------------------------------------------------------------------------------


def test_wg8_t02_a_tie_pauses_and_asks_with_the_short_list_scores_and_reasons() -> None:
    world = World({"a": 0.85, "b": 0.85, "c": 0.1}, ["a", "b", "c"])
    result = world.run()
    assert result.status is ChooseRepoStatus.ASKED and result.url is None
    assert world.store.decided is None  # nothing is decided until the FDE answers
    asked = world.store.asked
    assert asked is not None and asked.kind is RepoQuestionKind.CLOSE_CANDIDATES
    assert [c.url for c in asked.candidates] == [
        "https://github.com/acme/a",
        "https://github.com/acme/b",
    ]
    assert [c.score for c in asked.candidates] == [0.85, 0.85]
    assert asked.candidates[0].reasons == ["a fits"]
    assert asked.asked_at == NOW and asked.sub_skill_id == str(world.inp.sub_skill_id)


def test_wg8_t02_a_weak_match_pauses_and_asks() -> None:
    world = World({"a": 0.5}, ["a"])
    assert world.run().status is ChooseRepoStatus.ASKED
    assert world.store.asked is not None and world.store.asked.kind is RepoQuestionKind.WEAK_MATCH


def test_wg8_t02_a_second_call_without_an_answer_asks_again_but_never_rescored() -> None:
    world = World({"a": 0.85, "b": 0.85}, ["a", "b"])
    world.run()
    assert world.run().status is ChooseRepoStatus.ASKED  # a retried activity or a replay
    assert len(world.scout.calls) == 1  # one model call, one stored question


# --- WG8-T03 ------------------------------------------------------------------------------------


def test_wg8_t03_the_answer_is_recorded_with_who_and_when_and_drives_the_result() -> None:
    world = World({"a": 0.85, "b": 0.85}, ["a", "b"])
    world.run()
    result = world.run(answer(RepoAnswerAction.CHOOSE, "https://github.com/acme/b"))
    assert result.status is ChooseRepoStatus.DECIDED and result.outcome is RepoOutcome.REPO
    assert result.url == "https://github.com/acme/b"
    decision = world.store.decided
    assert decision is not None and decision.decided_by is DecidedBy.FDE
    assert decision.actor_id == FDE and decision.decided_at == NOW
    assert decision.chosen is not None and decision.chosen.url == "https://github.com/acme/b"
    assert {c.url for c in decision.candidates} == {
        "https://github.com/acme/a",
        "https://github.com/acme/b",
    }


def test_wg8_t03_an_answer_again_after_the_decision_changes_nothing() -> None:
    world = World({"a": 0.85, "b": 0.85}, ["a", "b"])
    world.run()
    first = world.run(answer(RepoAnswerAction.CHOOSE, "https://github.com/acme/b"))
    again = world.run(answer(RepoAnswerAction.NO_REPO))  # a replayed signal, a retried activity
    assert again == first


def test_wg8_t03_a_choice_off_the_short_list_is_refused_and_records_nothing() -> None:
    world = World({"a": 0.85, "b": 0.85}, ["a", "b"])
    world.run()
    with pytest.raises(ApplicationError) as refused:
        world.run(answer(RepoAnswerAction.CHOOSE, "https://github.com/evil/other"))
    assert refused.value.type == "RepoAnswerInvalid" and refused.value.non_retryable
    assert world.store.decided is None


def test_wg8_t03_an_answer_with_no_question_is_refused() -> None:
    world = World({"a": 0.9}, ["a"])
    with pytest.raises(ApplicationError) as refused:
        world.run(answer(RepoAnswerAction.NO_REPO))
    assert refused.value.type == "RepoQuestionMissing"


# --- WG8-T04 ------------------------------------------------------------------------------------


def test_wg8_t04_no_repo_falls_back_to_generation() -> None:
    world = World({"a": 0.85, "b": 0.85}, ["a", "b"])
    world.run()
    result = world.run(answer(RepoAnswerAction.NO_REPO))
    assert result.status is ChooseRepoStatus.DECIDED and result.outcome is RepoOutcome.GENERATED
    assert result.url is None
    decision = world.store.decided
    assert decision is not None and decision.chosen is None
    assert decision.decided_by is DecidedBy.FDE and decision.actor_id == FDE


def test_wg8_t04_with_no_candidate_the_fde_is_asked_and_can_say_there_is_none() -> None:
    world = World({}, [])
    assert world.run().status is ChooseRepoStatus.ASKED
    asked = world.store.asked
    assert asked is not None and asked.kind is RepoQuestionKind.NO_CANDIDATE
    assert asked.candidates == [] and world.scout.calls == []  # an empty catalogue asks no model
    result = world.run(answer(RepoAnswerAction.NO_REPO))
    assert result.outcome is RepoOutcome.GENERATED


def test_wg8_t04_with_no_candidate_the_fde_may_name_a_repository() -> None:
    world = World({}, [])
    world.run()
    result = world.run(answer(RepoAnswerAction.CHOOSE, "https://github.com/me/mine"))
    assert result.outcome is RepoOutcome.REPO and result.url == "https://github.com/me/mine"
    decision = world.store.decided
    assert decision is not None
    assert [c.source for c in decision.candidates] == [RepoSource.FDE]


# --- failures -----------------------------------------------------------------------------------


def test_wg8_a_model_that_cannot_answer_propagates_and_writes_nothing() -> None:
    world = World(BudgetExceeded("tenant budget spent"), ["a"])
    with pytest.raises(BudgetExceeded):
        world.run()
    assert world.store.asked is None and world.store.decided is None


# --- the audit-trail store ----------------------------------------------------------------------


def test_wg8_the_sql_store_keeps_one_row_per_build_and_reads_it_back() -> None:
    sessions: sessionmaker[Session] = pipeline_sessions()
    store = SqlChoiceStore(sessions)
    inp = build_input()
    question = RepoQuestion(
        sub_skill_id=str(inp.sub_skill_id),
        kind=RepoQuestionKind.NO_CANDIDATE,
        candidates=[],
        asked_at=NOW,
    )
    assert store.question(inp) is None and store.decision(inp) is None
    store.put_question(inp, question)
    assert store.question(inp) == question
    other_build = build_input(
        sub_skill_id=inp.sub_skill_id,
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        build_no=2,
    )
    assert store.question(other_build) is None  # a rebuild asks its own question
    decision = RepoDecision(
        sub_skill_id=str(inp.sub_skill_id),
        outcome=RepoOutcome.GENERATED,
        decided_by=DecidedBy.FDE,
        actor_id=FDE,
        decided_at=NOW,
        reason="The FDE said no repository fits: the code is generated.",
    )
    store.put_decision(inp, decision)
    assert store.decision(inp) == decision
    other_tenant = build_input(sub_skill_id=inp.sub_skill_id)
    assert store.decision(other_tenant) is None  # rows are tenant-scoped
