"""The token budget of a build (ADR 0028, decision 6).

Three scopes have a cap: one Sub-Skill build, one Workflow build and one knowledge build. The count
is a query over `llm_calls` (no second source of truth); this module only fixes the numbers and says
what a count means. Alerts fire once per level: at the first percentage, at the second, and the
build stops at the cap. The cap may be raised once per build by the FDE or an Admin.

The defaults are the ones of ADR 0028, marked there as pending the owner's answer.
"""

from enum import StrEnum
from typing import Annotated, Any, Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbam_contracts._jsonschema import contract_schema

DEFAULT_SUB_SKILL_TOKENS: Final = 200_000
DEFAULT_WORKFLOW_TOKENS: Final = 2_000_000
DEFAULT_KNOWLEDGE_TOKENS: Final = 30_000_000
MAX_CAP: Final = 1_000_000_000
# Aliases whose tokens count against the knowledge build's cap (Cognee's own two, ADR 0028); every
# other alias counts against the Workflow build's.
KNOWLEDGE_ALIASES: Final = frozenset({"sbam-knowledge", "sbam-embed"})
BUILD_PREFIX: Final = "build-"  # the Temporal workflow id of one Sub-Skill build

Tokens = Annotated[int, Field(strict=True, ge=1, le=MAX_CAP)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BudgetScope(StrEnum):
    SUB_SKILL = "SUB_SKILL"
    WORKFLOW = "WORKFLOW"
    KNOWLEDGE = "KNOWLEDGE"


class BudgetLevel(StrEnum):
    OK = "OK"
    FIRST_ALERT = "FIRST_ALERT"  # the FDE is told
    SECOND_ALERT = (
        "SECOND_ALERT"  # the FDE and the Admin are told; the cheaper model tier takes over
    )
    STOPPED = "STOPPED"  # the cap is reached: no new model call starts


class BudgetCaps(_Strict):
    """A project's caps.

    Every field has the ADR 0028 default, so `BudgetCaps()` is the platform default."""

    sub_skill_tokens: Tokens = DEFAULT_SUB_SKILL_TOKENS
    workflow_tokens: Tokens = DEFAULT_WORKFLOW_TOKENS
    knowledge_tokens: Tokens = DEFAULT_KNOWLEDGE_TOKENS
    first_alert_pct: Annotated[int, Field(strict=True, ge=1, le=98)] = 50
    second_alert_pct: Annotated[int, Field(strict=True, ge=2, le=99)] = 80

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.first_alert_pct >= self.second_alert_pct:
            raise ValueError("first_alert_pct must be below second_alert_pct")
        if self.sub_skill_tokens > self.workflow_tokens:
            raise ValueError("a Sub-Skill cap above the Workflow cap can never be reached")
        return self

    def cap(self, scope: BudgetScope) -> int:
        return {
            BudgetScope.SUB_SKILL: self.sub_skill_tokens,
            BudgetScope.WORKFLOW: self.workflow_tokens,
            BudgetScope.KNOWLEDGE: self.knowledge_tokens,
        }[scope]

    def level(self, scope: BudgetScope, used: int) -> BudgetLevel:
        """Where `used` tokens stand against this scope's cap."""
        cap = self.cap(scope)
        if used >= cap:
            return BudgetLevel.STOPPED
        if used * 100 >= cap * self.second_alert_pct:
            return BudgetLevel.SECOND_ALERT
        if used * 100 >= cap * self.first_alert_pct:
            return BudgetLevel.FIRST_ALERT
        return BudgetLevel.OK


class BudgetState(_Strict):
    """What one build has used, as the workbench meter and the alerts read it."""

    scope: Annotated[BudgetScope, Field(strict=False)]
    used: Annotated[int, Field(strict=True, ge=0)]
    cap: Tokens
    level: Annotated[BudgetLevel, Field(strict=False)]
    raised_once: bool = (
        False  # the cap was raised for this build already; it cannot be raised again
    )
    # The build whose calls were last counted in this scope: the one a raise names (#776).
    build_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None

    @model_validator(mode="after")
    def _level_matches_usage(self) -> Self:
        if (self.used >= self.cap) != (self.level is BudgetLevel.STOPPED):
            raise ValueError("level must be STOPPED exactly when used has reached cap")
        return self


class ProjectTokenCaps(_Strict):
    """`GET`/`PUT /admin/projects/{id}/token-caps` (#776): the caps of one project.

    `row_version` is the ETag (`If-Match` on PUT); it is 0 while nothing is stored, and then
    `is_default` is true and `caps` are the platform defaults."""

    caps: BudgetCaps
    row_version: Annotated[int, Field(strict=True, ge=0)]
    is_default: bool


class TokenMeter(_Strict):
    """`GET /projects/{id}/token-budget` (#776): used against the cap per scope, for the meter."""

    scopes: list[BudgetState]
    first_alert_pct: Annotated[int, Field(strict=True, ge=1, le=98)]
    second_alert_pct: Annotated[int, Field(strict=True, ge=2, le=99)]


class RaiseCap(_Strict):
    """`POST .../token-budget/raise` (#776): a new cap for one scope, once per build.

    `build_id` is the build the meter names for that scope (the Temporal workflow id of its calls).
    """

    scope: Annotated[BudgetScope, Field(strict=False)]
    build_id: Annotated[str, Field(min_length=1, max_length=255)]
    tokens: Tokens


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/budget/`."""
    return {
        "caps": contract_schema(BudgetCaps, "budget/caps"),
        "state": contract_schema(BudgetState, "budget/state"),
        "project_caps": contract_schema(ProjectTokenCaps, "budget/project_caps"),
        "meter": contract_schema(TokenMeter, "budget/meter"),
        "raise": contract_schema(RaiseCap, "budget/raise"),
    }
