"""ISATVON pass 1 (TD §10.2, §13.2, E8.2): Macro Skills from the approved SOW.

`sbam-isatvon` answers a `MacroExtraction`: one proposal per Macro Skill, in order, each naming the
SOW sections it covers (`sow_refs`: module and feature ids of the approved SowDocument) and, when
one fits, an Agents-master entry (`agent_ref`, AG-*). The model never chooses ids or state: SBAM
numbers the macros by order (`MSnn_<Name>`, nn from 01), drops an `agent_ref` the project's master
snapshot doesn't hold (with a warning), and stores them PENDING. Extra keys are refused, so text in
the SOW or the knowledge cannot add fields.

`MacroSkillOut` is a stored Macro Skill as the API returns it.
"""

from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import MACRO_CATEGORY, ShortText, Text
from sbam_contracts.state_machines import AgentState

MAX_MACROS = 99  # `nn` is two digits


class MacroProposal(BaseModel):
    """One Macro Skill as the model proposes it (a major module / functional area)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: ShortText
    description: Text
    sow_refs: Annotated[list[ShortText], Field(min_length=1)]  # SOW module / feature ids
    agent_ref: ShortText | None = None  # Agents master, AG-*


class MacroExtraction(BaseModel):
    """The `sbam-isatvon` answer of pass 1 (prompt `isatvon-macros`)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    macros: Annotated[list[MacroProposal], Field(min_length=1, max_length=MAX_MACROS)]


class MacroSkillOut(BaseModel):
    """A stored Macro Skill. `validation_errors` holds warnings SBAM recorded while storing it
    (e.g. an `agent_ref` missing from the master snapshot, set to null)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workflow_id: UUID
    sow_version_id: UUID
    macro_category: Annotated[str, Field(pattern=MACRO_CATEGORY.pattern)]
    name: str
    description: str
    order: Annotated[int, Field(ge=1, le=MAX_MACROS)]
    sow_refs: list[str]
    agent_ref: str | None
    state: AgentState
    validation_errors: list[str]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/isatvon/`."""
    models: dict[str, type[BaseModel]] = {
        "macro_extraction": MacroExtraction,
        "macro_skill": MacroSkillOut,
    }
    return {stem: contract_schema(model, f"isatvon/{stem}") for stem, model in models.items()}
