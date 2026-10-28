"""SowDocument contract: the SOW the FDE approves, shaped for mechanical ISATVON breakdown.

TD §10.6, issue E0.5. The SOW has nine sections in a fixed order. Each module becomes a future
Agent (TD: Macro Skill) and each feature item a future Sub-Skill, so a feature item carries exactly
what an ISATVON 1.4 definition needs: trigger, action, check, bound, inputs/state, tools (action vs
check), tables, success criteria, output, handoff record and notifications.

Rules worth knowing
  * Strict: no coercion, unknown fields rejected. Platform fields (status, approved_by, sha256,
    version) are not part of the document, so an LLM or an editor save cannot set them.
  * Every section and every feature item carries at least one citation
    `{okf_ref_id, document_version_id (UUID), location}`.
  * Feature and module ids are stable across SOW versions (they drive scope-change impact) and must
    be unique. Every module needs at least one feature; a feature's action and check tools never
    overlap (decoupling, as ISATVON rule 4).
  * Text is data: control characters are stripped, lengths are capped, and markup such as
    `<script>` is kept as plain text, to be escaped when rendered.
  * Regenerate the committed JSON Schema from `server/` with `python -m sbam_contracts.sow`.
"""

import json
import sys
from collections import Counter
from datetime import date as _Date  # noqa: N812 - the field below is named `date`
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import MAX_ITERATIONS_RANGE, RULE_REF, ShortText, Text

SECTIONS: Final = (
    "overview",
    "users_authority",
    "scope",
    "modules",
    "features",
    "data_integrations",
    "rules_constraints",
    "deliverables_acceptance",
    "assumptions_open_points",
)
SCHEMA_DIR: Final = Path(__file__).resolve().parent.parent / "schemas" / "sow"

ModuleId = Annotated[str, Field(strict=True, pattern=r"^M-[A-Za-z0-9][A-Za-z0-9.-]*$")]
FeatureId = Annotated[str, Field(strict=True, pattern=r"^F-[A-Za-z0-9][A-Za-z0-9.-]*$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --- Citations ------------------------------------------------------------------------------------


class Location(_Strict):
    page: int | None = Field(default=None, ge=1)
    line: int | None = Field(default=None, ge=1)
    sheet: ShortText | None = None
    cell: ShortText | None = None

    @model_validator(mode="after")
    def _somewhere(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("location needs at least one of page, line, sheet, cell")
        return self


class Citation(_Strict):
    okf_ref_id: ShortText  # OKF chunk or entity id
    document_version_id: Annotated[UUID, Field(strict=False)]  # UUID string from JSON
    location: Location


class FdeCitation(_Strict):
    """A statement the FDE sourced themselves: no `okf_ref_id`, so nothing to look up in the bundle.

    The API stamps `author` and `date` on save (an editor cannot sign as someone else or backdate).
    """

    kind: Literal["fde"]
    author: ShortText
    date: Annotated[_Date, Field(strict=False)]  # ISO string from JSON
    note: Text | None = None


def _citation_kind(value: Any) -> str:
    """Stored and generated documents have no `kind` on a bundle citation (their hashes stay)."""
    kind = value.get("kind") if isinstance(value, dict) else getattr(value, "kind", None)
    return "fde" if kind == "fde" else "okf"


AnyCitation = Annotated[
    Annotated[Citation, Tag("okf")] | Annotated[FdeCitation, Tag("fde")],
    Discriminator(_citation_kind),
]
Citations = Annotated[list[AnyCitation], Field(min_length=1)]


class _Cited(_Strict):
    citations: Citations


# --- Sections 1-3 ---------------------------------------------------------------------------------


class Overview(_Cited):
    client: ShortText
    objective: Text
    background: Text
    topic: Text
    history: Text | None = None


class Role(_Strict):
    role: ShortText
    decision_rights: Text


class UsersAuthority(_Cited):
    roles: list[Role] = Field(min_length=1)


class Scope(_Cited):
    in_scope: list[Text] = Field(min_length=1)
    out_of_scope: list[Text]


# --- Sections 4-5: modules and feature items ------------------------------------------------------


class Module(_Cited):
    """A future Agent (TD: Macro Skill)."""

    id: ModuleId
    name: ShortText
    purpose: Text
    agent_role: ShortText


class Bound(_Strict):
    """Retry policy; becomes the Sub-Skill's max_iterations and escalation."""

    max_attempts: int = Field(ge=MAX_ITERATIONS_RANGE[0], le=MAX_ITERATIONS_RANGE[1])
    on_exhaustion: Text


class FeatureTools(_Strict):
    action: list[ShortText] = Field(min_length=1)
    check: list[ShortText] = Field(min_length=1)

    @model_validator(mode="after")
    def _decoupled(self) -> Self:
        shared = sorted({t.casefold() for t in self.action} & {t.casefold() for t in self.check})
        if shared:
            raise ValueError(f"check tool must differ from action tool: {', '.join(shared)}")
        return self


class FeatureItem(_Cited):
    """A future Sub-Skill. Fields map onto ISATVON 1.4 I/S/A/T/V/O/N."""

    id: FeatureId
    module_id: ModuleId
    name: ShortText
    trigger: Text
    action: Text
    check: Text
    bound: Bound
    inputs_state: list[Text] = Field(min_length=1)
    external_data: list[Text]
    tools: FeatureTools
    tables: list[ShortText]
    success_criteria: list[Text] = Field(min_length=1)
    output: Text
    handoff: Text
    notifications: list[Text]


# --- Sections 6-9 ---------------------------------------------------------------------------------


class DataIntegrations(_Cited):
    sources: list[Text]
    apis: list[Text]
    tables: list[ShortText]


class RulesConstraints(_Cited):
    rule_refs: list[ShortText]  # Rules master ids, RULE-*
    constraints: list[Text]


class DeliverablesAcceptance(_Cited):
    deliverables: list[Text] = Field(min_length=1)
    acceptance_criteria: list[Text] = Field(min_length=1)


class AssumptionsOpenPoints(_Cited):
    assumptions: list[Text]
    open_points: list[Text]


# --- Document -------------------------------------------------------------------------------------


class SowDocument(_Strict):
    """The nine SOW sections of TD §10.6, in order."""

    overview: Overview
    users_authority: UsersAuthority
    scope: Scope
    modules: list[Module] = Field(min_length=1)
    features: list[FeatureItem] = Field(min_length=1)
    data_integrations: DataIntegrations
    rules_constraints: RulesConstraints
    deliverables_acceptance: DeliverablesAcceptance
    assumptions_open_points: AssumptionsOpenPoints

    @model_validator(mode="before")
    @classmethod
    def _section_order(cls, data: Any) -> Any:
        if isinstance(data, dict) and set(data) == set(SECTIONS) and tuple(data) != SECTIONS:
            raise ValueError(f"sections must be in TD §10.6 order: {', '.join(SECTIONS)}")
        return data

    @model_validator(mode="after")
    def _ids(self) -> Self:
        module_ids = [m.id for m in self.modules]
        for dup in _duplicates(module_ids):
            raise ValueError(f"duplicate module id {dup}")
        for dup in _duplicates([f.id for f in self.features]):
            raise ValueError(f"duplicate feature id {dup}")
        known = set(module_ids)
        for feature in self.features:
            if feature.module_id not in known:
                raise ValueError(f"feature {feature.id} names unknown module {feature.module_id}")
        used = {f.module_id for f in self.features}
        for module_id in module_ids:
            if module_id not in used:
                raise ValueError(f"module {module_id} requires at least 1 feature")
        for ref in self.rules_constraints.rule_refs:
            if not RULE_REF.match(ref):
                raise ValueError(f"rule_refs: {ref!r} is not a Rules master id RULE-*")
        return self

    def features_of(self, module_id: str) -> list[FeatureItem]:
        return [f for f in self.features if f.module_id == module_id]


def _duplicates(ids: list[str]) -> list[str]:
    return [i for i, n in Counter(ids).items() if n > 1]


# --- Breakdown hints ------------------------------------------------------------------------------


def breakdown_hints(doc: SowDocument) -> list[dict[str, Any]]:
    """Module → future Agent, feature → future Sub-Skill, with each feature field placed on its
    ISATVON 1.4 element. Hints only: the isatvon stage (LLM + FDE grid) produces the real
    definitions.
    """
    return [
        {
            "module_id": module.id,
            "agent_name": module.name,
            "agent_role": module.agent_role,
            "sub_skills": [
                {
                    "feature_id": f.id,
                    "name": f.name,
                    "i_trigger": f.trigger,
                    "i_action": f.action,
                    "i_check": f.check,
                    "i_bound": (
                        f"Up to {f.bound.max_attempts} attempts, then {f.bound.on_exhaustion}"
                    ),
                    "s_inputs_state": list(f.inputs_state),
                    "s_external_data": list(f.external_data),
                    "a_action_step": f.action,
                    "a_acceptance_check": f.check,
                    "t_action_tools": list(f.tools.action),
                    "t_check_tools": list(f.tools.check),
                    "v_max_iterations": f.bound.max_attempts,
                    "v_tables": list(f.tables),
                    "o_output": f.output,
                    "o_success_criteria": list(f.success_criteria),
                    "o_handoff": f.handoff,
                    "n_notifications": list(f.notifications),
                }
                for f in doc.features_of(module.id)
            ],
        }
        for module in doc.modules
    ]


# --- JSON Schema ----------------------------------------------------------------------------------


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/sow/`."""
    return {"sow_document": contract_schema(SowDocument, "sow/sow_document")}


def write_schemas(directory: Path = SCHEMA_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for stem, schema in schemas().items():
        path = directory / f"{stem}.schema.json"
        text = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in write_schemas():
        sys.stdout.write(f"wrote {written_path}\n")
