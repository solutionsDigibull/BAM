"""Is the platform ready to do real work: `GET /preflight`.

Each check says whether something a build depends on works, and what to do when it does not. No
hosts, keys or provider messages: only a status, a short reason code and a fix an admin can act on.
"""

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema


class CheckStatus(StrEnum):
    OK = "ok"
    MOCK = "mock"  # the development mock answers: fine to click through, no real results
    DEGRADED = "degraded"  # slow or answering oddly; work may fail
    DOWN = "down"  # work that needs it will fail


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=r"^[a-z_]{1,40}$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    status: CheckStatus
    reason: Annotated[str, Field(max_length=80)] | None = None  # a code, e.g. `credentials_refused`
    fix: Annotated[str, Field(max_length=300)] | None = None  # what an admin does about it


class Preflight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CheckStatus  # the worst of the checks (mock counts as ok-with-a-label)
    checks: list[PreflightCheck]
    checked_at: Annotated[str, Field(min_length=10, max_length=40)]  # ISO 8601, UTC


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/preflight/`."""
    models: dict[str, type[BaseModel]] = {"preflight": Preflight, "check": PreflightCheck}
    return {stem: contract_schema(model, f"preflight/{stem}") for stem, model in models.items()}
