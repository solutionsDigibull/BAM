"""What a project has spent on model calls: `GET /projects/{project_id}/llm-usage`.

Read from `llm_calls`, which records every attempt, failed ones too (they were billed). Money is a
string with four decimals so no float rounding reaches the screen.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema

Money = Annotated[str, Field(pattern=r"^\d+\.\d{4}$")]


class LlmUsageStage(BaseModel):
    """One model alias (`sbam-sow`, `sbam-knowledge`, ...): what it was used for is in its name."""

    model_config = ConfigDict(extra="forbid")

    alias: Annotated[str, Field(min_length=1, max_length=100)]
    calls: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]
    tokens_in: Annotated[int, Field(ge=0)]
    tokens_out: Annotated[int, Field(ge=0)]
    cost_usd: Money


class LlmUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_usd: Money
    calls: Annotated[int, Field(ge=0)]
    tokens_in: Annotated[int, Field(ge=0)]
    tokens_out: Annotated[int, Field(ge=0)]
    stages: list[LlmUsageStage]  # most expensive first


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/llm_usage/`."""
    models: dict[str, type[BaseModel]] = {"usage": LlmUsage, "stage": LlmUsageStage}
    return {stem: contract_schema(model, f"llm_usage/{stem}") for stem, model in models.items()}
