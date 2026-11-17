"""#759: the repo scout helper, with a fake model (no model is called, nothing is wired)."""

import asyncio
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from sbam_contracts.repo_catalog import CatalogCandidate, RepoScoutAnswer
from sbam_contracts.repo_decision import RepoSource
from sbam_workers.build.repo_scout import ALIAS, PROMPT, ScoredRepo, request_context, score_repos
from sbam_workers.llm.budget import BudgetExceeded
from sbam_workers.llm.client import LlmResult
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope

SCOPE = LlmScope(uuid.uuid4(), uuid.uuid4(), "corr", "wf", "act")


def candidate(name: str, notes: str = "") -> CatalogCandidate:
    return CatalogCandidate(
        id=f"c-{name}",
        url=f"https://github.com/acme/{name}",
        notes=notes,
        source=RepoSource.ADMIN_AGENTS,
        master_entry_id="m-1",
        stable_id=f"s-{name}",
        agent_name=name,
    )


class FakeScout:
    def __init__(self, reply: dict[str, Any] | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        alias: str,
        prompt_id: str,
        output_type: type[RepoScoutAnswer],
        context: Mapping[str, str],
        scope: LlmScope,
        *,
        validate: Callable[[RepoScoutAnswer], tuple[str, ...]] | None = None,
        subject: Mapping[str, str] | None = None,
    ) -> LlmResult[RepoScoutAnswer]:
        self.calls.append({"alias": alias, "prompt": prompt_id, "context": dict(context)})
        if isinstance(self.reply, Exception):
            raise self.reply
        out = RepoScoutAnswer.model_validate(self.reply)
        self.validate = validate
        self.output = out
        return LlmResult(out, uuid.uuid4(), "t", attempts=1)


def score(scout: FakeScout, entries: list[CatalogCandidate]) -> list[ScoredRepo]:
    return asyncio.run(score_repos(scout, "sell widgets", {"skill": "x"}, entries, SCOPE))


def test_wg11_scores_come_back_best_first_one_per_catalogue_entry() -> None:
    a, b, c = candidate("a"), candidate("b"), candidate("c")
    scout = FakeScout(
        {
            "scores": [
                {"candidate_id": "c-b", "score": 0.9, "reasons": ["notes name the API"]},
                {"candidate_id": "c-a", "score": 0.3},
            ]
        }
    )
    rows = score(scout, [a, b, c])
    assert [r.candidate_id for r in rows] == ["c-b", "c-a", "c-c"]
    assert rows[0] == ScoredRepo("c-b", b.url, 0.9, ("notes name the API",))
    assert rows[2].score == 0.0 and rows[2].reasons == ()  # left out: no score is a 0.0


def test_wg11_an_unknown_id_is_never_used_and_the_check_that_re_asks_names_it() -> None:
    a = candidate("a")
    scout = FakeScout(
        {"scores": [{"candidate_id": "c-a", "score": 0.5}, {"candidate_id": "ghost", "score": 1.0}]}
    )
    rows = score(scout, [a])
    assert [r.candidate_id for r in rows] == ["c-a"]  # the ghost id never becomes a row
    assert scout.validate is not None
    assert scout.validate(scout.output) == ("scores: 'ghost' is not an id in `candidates`",)


def test_wg11_ties_keep_the_catalogue_order() -> None:
    entries = [candidate("a"), candidate("b"), candidate("c")]
    scout = FakeScout({"scores": [{"candidate_id": e.id, "score": 0.4} for e in entries]})
    assert [r.candidate_id for r in score(scout, entries)] == ["c-a", "c-b", "c-c"]


def test_wg11_an_empty_catalogue_asks_nothing() -> None:
    scout = FakeScout({"scores": []})
    assert score(scout, []) == [] and scout.calls == []


def test_wg11_the_scout_gets_its_alias_prompt_and_only_the_catalogues_words() -> None:
    scout = FakeScout({"scores": []})
    score(scout, [candidate("a", notes="Stripe webhooks")])
    [call] = scout.calls
    assert (call["alias"], call["prompt"]) == (ALIAS, PROMPT) == ("sbam-repo-scout", "repo-scout")
    assert set(call["context"]) == {"goal", "sub_skill", "candidates"}
    assert "Stripe webhooks" in call["context"]["candidates"]
    assert (
        "master_entry_id" not in call["context"]["candidates"]
    )  # nothing beyond the entry's words


def test_wg11_a_model_error_propagates_for_the_caller_to_decide() -> None:
    with pytest.raises(BudgetExceeded):
        score(FakeScout(BudgetExceeded("token budget reached")), [candidate("a")])


def test_wg11_the_prompt_ships_with_its_alias_and_holds_no_interpolated_data() -> None:
    for prompt_id, alias in (("repo-scout", "sbam-repo-scout"), ("review-diff", "sbam-reviewer")):
        prompt = PromptRegistry().get(prompt_id)
        assert prompt.alias == alias
        assert "{" not in prompt.text.replace("{`", "")  # no template slots: data is context only


def test_wg11_the_request_context_is_plain_blocks() -> None:
    context = request_context("g", {"a": 1}, [candidate("a")])
    assert list(context) == ["goal", "sub_skill", "candidates"]
