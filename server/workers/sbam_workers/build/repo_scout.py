"""The repo scout (#759, WG-11): how well each catalogued repository fits one Sub-Skill.

`score_repos` asks `sbam-repo-scout` (prompt `repo-scout`) to score the project's catalogue
(`sbam_contracts.repo_catalog.CatalogCandidate`, WG-6) against the Sub-Skill, and returns one
`ScoredRepo` per catalogue entry. It takes the model as an argument (`Scout`), so tests run it with
a fake and never reach a model. The answer is advice: an id that is not in the catalogue is refused
(the call is re-asked, the id is never used), a candidate the model left out scores 0.0, and the
caller (#756, the choose-a-repo step) still applies the ADR 0028 rules to the result. Nothing here
fetches a repository, and nothing calls it yet.

Errors of the model side (`SchemaInvalidAfterRetries`, `LlmTransportError`, `BudgetExceeded`, ...)
propagate: the caller decides whether a build goes on without a scout (it can: no repository is a
valid outcome). An empty catalogue asks nothing.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from sbam_contracts.repo_catalog import CatalogCandidate, RepoScoutAnswer

from sbam_workers.llm.client import LlmResult
from sbam_workers.llm.store import LlmScope

ALIAS: Final = "sbam-repo-scout"
PROMPT: Final = "repo-scout"


class Scout(Protocol):
    """The part of `LlmClient` this step uses (a fake in the tests)."""

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
    ) -> LlmResult[RepoScoutAnswer]: ...


@dataclass(frozen=True)
class ScoredRepo:
    candidate_id: str
    url: str
    score: float  # 0.0 to 1.0; 0.0 when the scout gave none
    reasons: tuple[str, ...]


def _entry(candidate: CatalogCandidate) -> dict[str, Any]:
    """What the scout sees of a candidate: the catalogue's own words, nothing else."""
    return {
        "id": candidate.id,
        "url": candidate.url,
        "ref": candidate.ref,
        "notes": candidate.notes,
        "source": candidate.source.value,
        "agent_name": candidate.agent_name,
    }


def request_context(
    goal: str, sub_skill: Mapping[str, Any], entries: Sequence[CatalogCandidate]
) -> dict[str, str]:
    """The `<data>` blocks of the request."""
    return {
        "goal": goal,
        "sub_skill": json.dumps(sub_skill, indent=1, sort_keys=True, ensure_ascii=False),
        "candidates": json.dumps([_entry(c) for c in entries], indent=1, ensure_ascii=False),
    }


def _unknown_ids(known: frozenset[str]) -> Callable[[RepoScoutAnswer], tuple[str, ...]]:
    def check(answer: RepoScoutAnswer) -> tuple[str, ...]:
        # codes and ids only: the re-ask message never carries model prose
        return tuple(
            f"scores: {s.candidate_id!r} is not an id in `candidates`"
            for s in answer.scores
            if s.candidate_id not in known
        )

    return check


async def score_repos(
    scout: Scout,
    goal: str,
    sub_skill: Mapping[str, Any],
    catalog_entries: Sequence[CatalogCandidate],
    scope: LlmScope,
) -> list[ScoredRepo]:
    """One `ScoredRepo` per catalogue entry, best first (ties keep the catalogue's order)."""
    if not catalog_entries:
        return []
    known = frozenset(c.id for c in catalog_entries)
    result = await scout.run(
        ALIAS,
        PROMPT,
        RepoScoutAnswer,
        request_context(goal, sub_skill, catalog_entries),
        scope,
        validate=_unknown_ids(known),
    )
    scored = {s.candidate_id: s for s in result.output.scores if s.candidate_id in known}
    rows = [
        ScoredRepo(
            c.id,
            c.url,
            scored[c.id].score if c.id in scored else 0.0,
            tuple(scored[c.id].reasons) if c.id in scored else (),
        )
        for c in catalog_entries
    ]
    return sorted(rows, key=lambda r: -r.score)  # sorted() is stable: ties keep catalogue order
