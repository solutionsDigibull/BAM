"""Admin invalidation of cached model answers (#421 part 5): `POST /admin/llm-cache/invalidate`.

A cached answer is addressed by a hash of the exact messages, so it cannot name the document it
came from; the two ways to find a bad one are the key (from a provenance row) or the prompt that
produced it. Exactly one form per request.
"""

from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbam_contracts._jsonschema import contract_schema


class LlmCacheInvalidate(BaseModel):
    """By `cache_key` (one answer) or by `prompt_id` (every answer of that prompt, optionally of
    one `prompt_version`), in the caller's tenant."""

    model_config = ConfigDict(extra="forbid")

    cache_key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    prompt_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    prompt_version: Annotated[str, Field(min_length=1, max_length=40)] | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        if (self.cache_key is None) == (self.prompt_id is None):
            raise ValueError("send either cache_key or prompt_id")
        if self.cache_key is not None and self.prompt_version is not None:
            raise ValueError("prompt_version goes with prompt_id, not with cache_key")
        if self.prompt_id is None and self.prompt_version is not None:
            raise ValueError("prompt_version needs prompt_id")
        return self


class LlmCacheInvalidated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invalidated: Annotated[int, Field(ge=0)]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/llm_cache/`."""
    models: dict[str, type[BaseModel]] = {
        "invalidate": LlmCacheInvalidate,
        "invalidated": LlmCacheInvalidated,
    }
    return {stem: contract_schema(model, f"llm_cache/{stem}") for stem, model in models.items()}
