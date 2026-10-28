"""The ISATVON workbench's data (TD §8.8, E8.4/E8.7): the Macro → Sub-Skill tree the grid shows and
the answers of an edit or a validation.

TD §8.8 lists the routes but leaves their bodies open ("tree", "Sub-Skill + validation_errors[]");
these are the shapes the workbench (#99) is built against; the edit endpoints are E8.4 (#94).

- `GET W/macro-skills` → `WorkbenchTree`. Each Sub-Skill row's `ETag` is its `content_sha256`.
- `PATCH W/sub-skills/{sub_id}` takes `SubSkillPatch` + `If-Match` → `WorkbenchSubSkill` (the whole
  row again, revalidated: an edit moves it to DRAFT, then VALID or INVALID with the violations).
- `POST W/isatvon/validate` → `ValidationReport`.
"""

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import RuleViolation, SubSkill
from sbam_contracts.runs import RunInput
from sbam_contracts.state_machines import SubSkillState, WorkflowState

MAX_HINT_CHARS: Final = 2000
ISATVON_REGENERATE: Final = (
    "IsatvonRegenerate"  # workflow type of POST W/{macro,sub}-skills/{id}/regenerate
)


def regenerate_workflow_id(entity_id: UUID) -> str:
    """One re-extraction at a time per Macro Skill / Sub-Skill (Temporal's FAIL conflict policy)."""
    return f"isatvon-regen-{entity_id}"


PLATFORM_OWNED = frozenset({"skill_metadata.skill_id"})  # read-only in the grid too (#98)
# Top-level fields of a Sub-Skill row a PATCH may never carry (422 READ_ONLY).
READ_ONLY_ROW_FIELDS = frozenset(
    {"id", "macro_id", "state", "content_sha256", "validation_errors", "sow_refs"}
)


class WorkbenchSubSkill(BaseModel):
    """One Sub-Skill row: ISATVON 1.4 content plus the platform fields the grid shows read-only."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    macro_id: UUID
    state: SubSkillState
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # each violation's `path` is a grid `field_path`, so the grid can mark that cell
    validation_errors: list[RuleViolation] = Field(default_factory=list)
    # the approved SOW's module / feature ids this Sub-Skill covers (its citations; read-only)
    sow_refs: list[str] = Field(default_factory=list)
    sub_skill: SubSkill


class WorkbenchMacro(BaseModel):
    """A Macro Skill (contract `Agent`) with its Sub-Skills in order."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    macro_category: str
    name: str
    description: str
    order: int = Field(ge=0)
    sub_skills: list[WorkbenchSubSkill]


class WorkbenchTree(BaseModel):
    """`GET W/macro-skills`. `workflow_state` and `sow_approved` decide whether the grid is editable
    and whether Generate can be offered; the server enforces both again (409/423)."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: UUID
    workflow_state: WorkflowState
    sow_approved: bool
    macros: list[WorkbenchMacro]


class SubSkillPatch(BaseModel):
    """`PATCH W/sub-skills/{sub_id}` (E8.4): a partial ISATVON 1.4 Sub-Skill and/or a new position.

    `changes` keys are dotted grid `field_path`s (e.g.
    `isatvon_definition.v_variables.max_iterations`; a leading `isatvon.` is read as
    `isatvon_definition.`); the server merges them into the row, revalidates and answers the whole
    row. `order` moves the Sub-Skill within its Macro Skill (the server renumbers the rest
    contiguously). Platform-owned fields (`skill_metadata.skill_id`, and a top-level `state`,
    `content_sha256`, `validation_errors`, `sow_refs`, `id`) are not editable: the server answers
    422 `READ_ONLY` with the field's path, so unknown top-level keys are kept (not rejected here)
    for it to name.
    """

    model_config = ConfigDict(extra="allow")

    changes: dict[str, Any] = Field(default_factory=dict)
    order: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _something_to_change(self) -> "SubSkillPatch":
        if not self.changes and self.order is None and not self.model_extra:
            raise ValueError("nothing to change: send `changes` and/or `order`")
        return self


class MacroSkillPatch(BaseModel):
    """`PATCH W/macro-skills/{macro_id}` (E8.4). `order` renumbers the other Macro Skills
    contiguously; the Macro Skill's `macro_category` and its Sub-Skills' ids never change."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    order: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _something_to_change(self) -> "MacroSkillPatch":
        if not self.model_fields_set:
            raise ValueError("nothing to change")
        return self


class SubSkillEdit(BaseModel):
    """One changed field of an edit (`sub_skill_edits`): `path` is the grid `field_path`."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    path: str
    old: Any
    new: Any
    by: str
    at: datetime


class SubSkillEditHistory(BaseModel):
    """`GET W/sub-skills/{sub_id}/edits`: oldest first, per changed field."""

    model_config = ConfigDict(extra="forbid")

    sub_skill_id: UUID
    edits: list[SubSkillEdit]


class RegenerateRequest(BaseModel):
    """`POST W/macro-skills/{id}/regenerate` and `POST W/sub-skills/{id}/regenerate` (E8.4): an
    optional short instruction the FDE typed, never document content."""

    model_config = ConfigDict(extra="forbid")

    hint: Annotated[str | None, Field(max_length=MAX_HINT_CHARS)] = None


class RegenerateInput(RunInput):
    """`IsatvonRegenerate`'s input: ids only; no `sub_skill_id` re-extracts the Macro Skill."""

    workflow_id: UUID
    macro_skill_id: UUID
    sub_skill_id: UUID | None = None
    hint: Annotated[str | None, Field(max_length=MAX_HINT_CHARS)] = None
    actor_id: Annotated[str, Field(min_length=1, max_length=100)]


class SubSkillValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    state: SubSkillState
    validation_errors: list[RuleViolation] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """`POST W/isatvon/validate`: every Sub-Skill of the Workflow, revalidated."""

    model_config = ConfigDict(extra="forbid")

    sub_skills: list[SubSkillValidation]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/isatvon/`."""
    models: dict[str, type[BaseModel]] = {
        "workbench_tree": WorkbenchTree,
        "workbench_sub_skill": WorkbenchSubSkill,
        "sub_skill_patch": SubSkillPatch,
        "macro_skill_patch": MacroSkillPatch,
        "sub_skill_edit_history": SubSkillEditHistory,
        "regenerate_request": RegenerateRequest,
        "regenerate_input": RegenerateInput,
        "validation_report": ValidationReport,
    }
    return {stem: contract_schema(model, f"isatvon/{stem}") for stem, model in models.items()}
