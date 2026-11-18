"""The `choose_repo` build stage (#756, WG-8): pick the repository a Sub-Skill's code starts from,
or ask the FDE (ADR 0028, decision 1).

Order of the rules, per Sub-Skill build:

1. A repository named in the Sub-Skill's ISATVON (`t_tech_stack.source_repos`) is used as it is: an
   AUTO decision, nothing is scored or asked.
2. Otherwise the project's Admin catalogue (WG-6) is scored by the repo scout (#759, `score_repos`):
   * no candidate: the question is whether the FDE has a repository (`NO_CANDIDATE`);
   * one clear winner (score at least `CLEAR_MIN` and ahead of the runner-up by `MARGIN`): used and
     recorded, an AUTO decision, nothing is asked;
   * a best score below `CLEAR_MIN` (`WEAK_MATCH`) or a runner-up too close (`CLOSE_CANDIDATES`):
     the question, with the short list, the scores and the reasons.
   `CLEAR_MIN` and `RELATED_MIN` are the bands of the repo-scout prompt (0.8 to 1.0 names the same
   job, 0.4 to 0.7 is related, below 0.4 is unrelated).
3. A question is recorded as the audit row `repo_question_asked` and the activity answers ASKED; the
   workflow waits for the FDE with no timeout (a reminder every 3 days) and calls this stage again
   with the FDE's answer, which is recorded as the `RepoDecision` row `repo_decided` (who, when).
   "No repository" is a GENERATED decision: the hybrid generator writes everything.

Nothing is fetched here and a catalogue URL is never trusted beyond what WG-6 already refused: the
FDE's own URL (only when the catalogue is empty) was checked by the API with the same parser. Every
row is keyed by (Sub-Skill, build), so a retried activity or a replay finds what it wrote and
answers from it: no second model call, no second question, one decision per build.

`ChooseRepo` takes its dependencies as arguments (inputs loader, store, scout, clock), so tests run
it with fakes; `wiring.py` builds the real ones. Errors of the model side propagate before anything
is written, and `SubSkillBuild` parks the build like any model stage.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from sbam_api.audit import repository as audit
from sbam_contracts.build import BuildStageInput
from sbam_contracts.repo_catalog import CatalogCandidate
from sbam_contracts.repo_choice import (
    ANSWER_INVALID,
    DECIDED,
    MAX_SHORT_LIST,
    QUESTION_ASKED,
    QUESTION_MISSING,
    ChooseRepoInput,
    ChooseRepoResult,
    ChooseRepoStatus,
    RepoAnswerAction,
    RepoAnswerSignal,
    RepoQuestion,
    RepoQuestionKind,
)
from sbam_contracts.repo_decision import (
    DecidedBy,
    RepoCandidate,
    RepoChoice,
    RepoDecision,
    RepoOutcome,
    RepoSource,
)
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.repo_scout import ScoredRepo, Scout, score_repos
from sbam_workers.llm.store import LlmScope

STAGE: Final = "choose_repo"
ENTITY: Final = "sub_skill"
SYSTEM_ACTOR: Final = "sbam-repo-chooser"
CLEAR_MIN: Final = 0.8  # "the evidence names the same job" band of the repo-scout prompt
RELATED_MIN: Final = 0.4  # "related" band: worth showing the FDE
MARGIN: Final = 0.15  # a winner leads the runner-up by at least this much
WEAK_SHOWN: Final = 3  # a weak match with nothing related still shows the best few


@dataclass(frozen=True)
class ChoiceInputs:
    """What the choice needs besides the audit trail: the goal, the Sub-Skill and the catalogue."""

    goal: str
    sub_skill: Mapping[str, Any]
    catalog: Sequence[CatalogCandidate]


@dataclass(frozen=True)
class Verdict:
    """The rules applied to the scores: a winner, or a question kind and its short list."""

    winner: ScoredRepo | None
    kind: RepoQuestionKind | None
    shortlist: tuple[ScoredRepo, ...]


class ChoiceStore(Protocol):
    """The audit rows of one build's choice (the SQL implementation is `SqlChoiceStore`)."""

    def question(self, inp: BuildStageInput) -> RepoQuestion | None: ...

    def decision(self, inp: BuildStageInput) -> RepoDecision | None: ...

    def put_question(self, inp: BuildStageInput, question: RepoQuestion) -> None: ...

    def put_decision(self, inp: BuildStageInput, decision: RepoDecision) -> None: ...


def question_key(inp: BuildStageInput) -> str:
    return f"{QUESTION_ASKED}:{inp.sub_skill_id}:{inp.build_no}"


def decision_key(inp: BuildStageInput) -> str:
    return f"{DECIDED}:{inp.sub_skill_id}:{inp.build_no}"


def stage_scope(inp: BuildStageInput, temporal_workflow_id: str) -> LlmScope:
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=temporal_workflow_id,
        activity_id=f"{STAGE}:{inp.sub_skill_id}:{inp.build_no}",
    )


def judge(scored: Sequence[ScoredRepo]) -> Verdict:
    """Apply the ADR 0028 rules to the scores (best first, as `score_repos` returns them)."""
    rows = sorted(scored, key=lambda r: -r.score)  # stable: ties keep the catalogue's order
    if not rows:
        return Verdict(None, RepoQuestionKind.NO_CANDIDATE, ())
    top, runner_up = rows[0], rows[1] if len(rows) > 1 else None
    if top.score >= CLEAR_MIN:
        if runner_up is None or top.score - runner_up.score >= MARGIN:
            return Verdict(top, None, (top,))
        kind = RepoQuestionKind.CLOSE_CANDIDATES
    else:
        kind = RepoQuestionKind.WEAK_MATCH
    related = [r for r in rows if r.score >= RELATED_MIN][:MAX_SHORT_LIST]
    return Verdict(None, kind, tuple(related or rows[:WEAK_SHOWN]))


def named_repos(sub_skill: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The `source_repos` of the Sub-Skill's T block; empty when it names none."""
    definition = sub_skill.get("isatvon_definition")
    tech = definition.get("t_tech_stack") if isinstance(definition, Mapping) else None
    repos = tech.get("source_repos") if isinstance(tech, Mapping) else None
    return [dict(r) for r in repos or [] if isinstance(r, Mapping) and r.get("url")]


def _candidate(row: ScoredRepo, source: RepoSource, by_id: Mapping[str, CatalogCandidate]) -> Any:
    found = by_id.get(row.candidate_id)
    return RepoCandidate(
        url=row.url,
        source=found.source if found is not None else source,
        score=min(1.0, max(0.0, row.score)),
        reasons=list(row.reasons)[:8],
    )


def _now() -> datetime:
    return datetime.now(UTC)


class SqlChoiceStore:
    """`ChoiceStore` on the audit trail: the rows are never edited, one per key."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def _after(self, inp: BuildStageInput, key: str) -> dict[str, Any] | None:
        with self.sessions() as session:
            row = audit.find_by_key(session, inp.tenant_id, ENTITY, str(inp.sub_skill_id), key)
            return None if row is None or row.after is None else dict(row.after)

    def question(self, inp: BuildStageInput) -> RepoQuestion | None:
        found = self._after(inp, question_key(inp))
        return None if found is None else RepoQuestion.model_validate(found, strict=False)

    def decision(self, inp: BuildStageInput) -> RepoDecision | None:
        found = self._after(inp, decision_key(inp))
        return None if found is None else RepoDecision.model_validate(found, strict=False)

    def _put(
        self, inp: BuildStageInput, action: str, actor: str, key: str, after: dict[str, Any]
    ) -> None:
        with self.sessions() as session:
            audit.record(
                session,
                tenant_id=inp.tenant_id,
                actor_id=actor,
                action=action,
                entity_type=ENTITY,
                entity_id=str(inp.sub_skill_id),
                correlation_id=inp.correlation_id,
                after=after,
                idempotency_key=key,
            )
            session.commit()

    def put_question(self, inp: BuildStageInput, question: RepoQuestion) -> None:
        self._put(
            inp,
            QUESTION_ASKED,
            SYSTEM_ACTOR,
            question_key(inp),
            question.model_dump(mode="json"),
        )

    def put_decision(self, inp: BuildStageInput, decision: RepoDecision) -> None:
        actor = decision.actor_id or SYSTEM_ACTOR
        self._put(inp, DECIDED, actor, decision_key(inp), decision.model_dump(mode="json"))


def result_of(decision: RepoDecision) -> ChooseRepoResult:
    return ChooseRepoResult(
        status=ChooseRepoStatus.DECIDED,
        outcome=decision.outcome,
        url=None if decision.chosen is None else decision.chosen.url,
    )


class ChooseRepo:
    """`choose_repo` with its dependencies injected (see the module docstring)."""

    def __init__(
        self,
        inputs: Callable[[BuildStageInput], Awaitable[ChoiceInputs]],
        store: ChoiceStore,
        scout: Scout,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.inputs = inputs
        self.store = store
        self.scout = scout
        self.clock = clock

    @activity.defn(name=STAGE)
    async def run(self, inp: ChooseRepoInput) -> ChooseRepoResult:
        decided = await asyncio.to_thread(self.store.decision, inp)
        if decided is not None:  # a retry, a replay or a new cycle: one decision per build
            return result_of(decided)
        asked = await asyncio.to_thread(self.store.question, inp)
        if inp.answer is not None:
            return await self._answered(inp, inp.answer, asked)
        if asked is not None:
            return ChooseRepoResult(status=ChooseRepoStatus.ASKED)
        return await self._choose(inp)

    # --- the first call: decide or ask ------------------------------------------------------------

    async def _choose(self, inp: BuildStageInput) -> ChooseRepoResult:
        given = await self.inputs(inp)
        named = named_repos(given.sub_skill)
        if named:  # rule 1: the ISATVON names it
            first = named[0]
            candidates = [
                RepoCandidate(
                    url=r["url"],
                    source=RepoSource.ISATVON,
                    score=1.0,
                    licence=r.get("licence"),
                    reasons=[],
                )
                for r in named
            ]
            return await self._record(
                inp,
                RepoOutcome.REPO,
                candidates,
                RepoChoice(url=first["url"], commit=first.get("commit")),
                "The repository is named in the Sub-Skill's ISATVON.",
            )
        workflow_id = activity.info().workflow_id
        if workflow_id is None:  # the replay key needs the parent workflow
            raise RuntimeError(f"{STAGE} runs only inside a workflow")
        scored = await score_repos(
            self.scout,
            given.goal,
            given.sub_skill,
            given.catalog,
            stage_scope(inp, workflow_id),
        )
        verdict = judge(scored)
        by_id = {c.id: c for c in given.catalog}
        if verdict.winner is not None:
            winner = verdict.winner
            candidate = _candidate(winner, RepoSource.ADMIN_AGENTS, by_id)
            return await self._record(
                inp,
                RepoOutcome.REPO,
                [candidate],
                RepoChoice(url=winner.url),
                f"One clear winner from the Admin catalogue (score {winner.score:.2f}).",
            )
        if verdict.kind is None:  # no winner always means a question
            raise RuntimeError("the verdict has neither a winner nor a question")
        question = RepoQuestion(
            sub_skill_id=str(inp.sub_skill_id),
            kind=verdict.kind,
            candidates=[_candidate(r, RepoSource.ADMIN_AGENTS, by_id) for r in verdict.shortlist],
            asked_at=self.clock(),
        )
        await asyncio.to_thread(self.store.put_question, inp, question)
        return ChooseRepoResult(status=ChooseRepoStatus.ASKED)

    async def _record(
        self,
        inp: BuildStageInput,
        outcome: RepoOutcome,
        candidates: list[RepoCandidate],
        chosen: RepoChoice | None,
        reason: str,
    ) -> ChooseRepoResult:
        decision = RepoDecision(
            sub_skill_id=str(inp.sub_skill_id),
            outcome=outcome,
            candidates=candidates,
            chosen=chosen,
            decided_by=DecidedBy.AUTO,
            decided_at=self.clock(),
            reason=reason,
        )
        await asyncio.to_thread(self.store.put_decision, inp, decision)
        return result_of(decision)

    # --- the call after the wait: record the FDE's answer -----------------------------------------

    async def _answered(
        self, inp: BuildStageInput, answer: RepoAnswerSignal, asked: RepoQuestion | None
    ) -> ChooseRepoResult:
        if asked is None:
            raise ApplicationError(
                "an answer with no question", type=QUESTION_MISSING, non_retryable=True
            )
        candidates = list(asked.candidates)
        chosen: RepoChoice | None = None
        if answer.action is RepoAnswerAction.CHOOSE:
            url = answer.url
            known = {c.url for c in candidates}
            if url is None:
                raise ApplicationError(
                    "choose needs a url", type=ANSWER_INVALID, non_retryable=True
                )
            if url not in known:
                if asked.kind is not RepoQuestionKind.NO_CANDIDATE:  # only the short list
                    raise ApplicationError(
                        "not on the short list", type=ANSWER_INVALID, non_retryable=True
                    )
                candidates.append(RepoCandidate(url=url, source=RepoSource.FDE, score=1.0))
            chosen = RepoChoice(url=url)
            reason = "The FDE chose this repository."
        else:
            reason = "The FDE said no repository fits: the code is generated."
        decision = RepoDecision(
            sub_skill_id=str(inp.sub_skill_id),
            outcome=RepoOutcome.REPO if chosen is not None else RepoOutcome.GENERATED,
            candidates=candidates,
            chosen=chosen,
            decided_by=DecidedBy.FDE,
            actor_id=answer.actor_id,
            decided_at=self.clock(),
            reason=reason,
        )
        await asyncio.to_thread(self.store.put_decision, inp, decision)
        return result_of(decision)
