"""The ISATVON patch a correction proposed and the FDE has to decide (#499, follows #446).

A correction may patch the Sub-Skill's ISATVON. One that changes what the Sub-Skill means (a looser
bound, a weaker gate) is *held*: the Sub-Skill waits in `NEEDS_FDE_REVIEW` instead of certifying.
These are the bodies of the three endpoints that let the FDE decide it:

- `GET W/sub-skills/{sub_id}/held-patch` -> `HeldPatchResponse`: the patch waiting for a decision,
  or `held: null`. The operations are shown as the build recorded them (RFC 6902 on
  `/isatvon_definition`); the rationale is the model's text and is shown as text, never as markup.
- `POST W/sub-skills/{sub_id}/held-patch/accept` -> `PatchDecided`: apply the patch to the Sub-Skill
  through the same validated, audited path as an FDE edit, then retry from it.
- `POST W/sub-skills/{sub_id}/held-patch/reject` -> `PatchDecided`: record the rejection and retry
  from the approved ISATVON.

Both decisions take an `Idempotency-Key`; accepting also takes the Sub-Skill's content hash as
`If-Match` (`HeldPatch.content_sha256`).
"""

from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.build import MAX_CHANGED_PATHS

MAX_HELD_OPS: Final = 50  # the corrector may not send more (`correction.MAX_PATCH_OPS`)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeldPatchOp(_Strict):
    """One RFC 6902 operation, as the build recorded it. `value` is empty for `remove`."""

    op: Literal["add", "remove", "replace", "test"]
    path: Annotated[str, Field(max_length=512)]
    value: JsonValue = None


class HeldPatch(_Strict):
    sub_skill_id: UUID
    # the Sub-Skill's `content_sha256`: send it as `If-Match` to accept (its edits' ETag)
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    attempt: Annotated[int, Field(ge=1)]  # the attempt the patch produced
    failed_gate: Annotated[str, Field(max_length=100)]  # the gate whose failure it answered
    rationale: Annotated[str, Field(max_length=4_000)]  # the model's reason; text, never markup
    changed_paths: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=MAX_CHANGED_PATHS
    )
    ops: list[HeldPatchOp] = Field(default_factory=list, max_length=MAX_HELD_OPS)
    isatvon_sha256_before: Annotated[str, Field(max_length=80)]  # the approved definition
    isatvon_sha256_after: Annotated[str, Field(max_length=80)]  # the definition with the patch


class HeldPatchResponse(_Strict):
    """`held` is null when no patch is waiting (none was proposed, or it was already decided)."""

    held: HeldPatch | None = None


class PatchDecision(_Strict):
    """Body of both decisions. A reason is optional for accepting and recorded for rejecting."""

    reason: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class PatchDecided(_Strict):
    sub_skill_id: UUID
    build_no: int
    decision: Literal["ACCEPTED", "REJECTED"]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/held_patch/`."""
    models: dict[str, type[BaseModel]] = {
        "held_patch_response": HeldPatchResponse,
        "patch_decision": PatchDecision,
        "patch_decided": PatchDecided,
    }
    return {stem: contract_schema(model, f"held_patch/{stem}") for stem, model in models.items()}
