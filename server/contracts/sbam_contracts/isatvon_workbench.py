"""The ISATVON workbench's data (TD §8.8, E8.4/E8.7): the Macro → Sub-Skill tree the grid shows and
the answers of an edit or a validation.

TD §8.8 lists the routes but leaves their bodies open ("tree", "Sub-Skill + validation_errors[]");
these are the shapes the workbench (#99) is built against until the endpoints land (#94).

- `GET W/macro-skills` → `WorkbenchTree`. Each Sub-Skill row's `ETag` is its `content_sha256`.
- `PATCH W/sub-skills/{sub_id}` takes `SubSkillPatch` + `If-Match` → `WorkbenchSubSkill` (the whole
  row again, revalidated: an edit moves it to DRAFT, then VALID or INVALID with the violations).
- `POST W/isatvon/validate` → `ValidationReport`.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import RuleViolation, SubSkill
from sbam_contracts.state_machines import SubSkillState, WorkflowState

PLATFORM_OWNED = frozenset({"skill_metadata.skill_id"})  # read-only in the grid too (#98)


class WorkbenchSubSkill(BaseModel):
    """One Sub-Skill row: ISATVON 1.4 content plus the platform fields the grid shows read-only."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    macro_id: UUID
    state: SubSkillState
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # each violation's `path` is a grid `field_path`, so the grid can mark that cell
    validation_errors: list[RuleViolation] = Field(default_factory=list)
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
    """`PATCH W/sub-skills/{sub_id}`: a partial ISATVON 1.4 Sub-Skill. Keys are dotted grid
    `field_path`s (e.g. `isatvon_definition.v_variables.max_iterations`); the server merges them
    into the row, revalidates and answers the whole row. `skill_metadata.skill_id` is never
    accepted."""

    model_config = ConfigDict(extra="forbid")

    changes: dict[str, Any] = Field(min_length=1)

    @field_validator("changes")
    @classmethod
    def _no_platform_fields(cls, changes: dict[str, Any]) -> dict[str, Any]:
        if PLATFORM_OWNED & changes.keys():
            raise ValueError("skill_metadata.skill_id is set by the platform, not edited")
        return changes


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
        "validation_report": ValidationReport,
    }
    return {stem: contract_schema(model, f"isatvon/{stem}") for stem, model in models.items()}
