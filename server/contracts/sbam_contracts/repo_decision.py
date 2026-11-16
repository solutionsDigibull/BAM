"""The repository decision of one Sub-Skill (ADR 0028, decision 1): the candidates considered,
how they scored, what was chosen and by whom.

The record is evidence. It is written once per build decision and never edited: a new decision is
a new record. `GENERATED` means no repository was used and the hybrid generator wrote the code.
A repository chosen by the FDE needs the FDE's id, so that "who chose" is always answerable.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import GitCommit, RepoUrl, ShortText, Text


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RepoSource(StrEnum):
    """Where a candidate came from: the priority order of ADR 0028, in the order it is tried."""

    ISATVON = "ISATVON"  # named in the Sub-Skill's T block
    ADMIN_AGENTS = "ADMIN_AGENTS"  # the Agents master, column `repo`
    ADMIN_TOOLING = "ADMIN_TOOLING"  # the Tooling master, column `links_path`
    FDE = "FDE"  # named by the FDE when asked in the workbench


class RepoOutcome(StrEnum):
    REPO = "REPO"  # the code starts from a repository
    GENERATED = "GENERATED"  # no repository: everything was generated


class DecidedBy(StrEnum):
    AUTO = "AUTO"  # one clear winner, no question asked
    FDE = "FDE"  # the FDE chose, or said there is no repository


class RepoCandidate(_Strict):
    url: RepoUrl
    source: Annotated[RepoSource, Field(strict=False)]
    score: Annotated[float, Field(ge=0.0, le=1.0)]
    licence: ShortText | None = None
    reasons: list[ShortText] = Field(default_factory=list, max_length=8)


class RepoChoice(_Strict):
    url: RepoUrl
    commit: GitCommit | None = None  # pinned by the fetcher; empty until then


class RepoDecision(_Strict):
    sub_skill_id: ShortText
    outcome: Annotated[RepoOutcome, Field(strict=False)]
    candidates: list[RepoCandidate] = Field(default_factory=list, max_length=20)
    chosen: RepoChoice | None = None
    decided_by: Annotated[DecidedBy, Field(strict=False)]
    actor_id: ShortText | None = None  # the FDE who decided; required when decided_by is FDE
    decided_at: datetime
    reason: Text

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("decided_at must carry a time zone")
        if self.outcome is RepoOutcome.REPO:
            if self.chosen is None:
                raise ValueError("a REPO decision needs the chosen repository")
            known = {c.url for c in self.candidates}
            if self.chosen.url not in known:
                raise ValueError("the chosen repository must be one of the candidates")
        elif self.chosen is not None:
            raise ValueError("a GENERATED decision has no chosen repository")
        if self.decided_by is DecidedBy.FDE and self.actor_id is None:
            raise ValueError("a decision made by the FDE needs actor_id")
        urls = [c.url for c in self.candidates]
        if len(set(urls)) != len(urls):
            raise ValueError("a candidate may appear once")
        return self


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/repo/`."""
    return {"decision": contract_schema(RepoDecision, "repo/decision")}
