"""Skill Manifest, Workflow manifest and loop_spec contracts (TD §11.1-11.5, issue E0.3).

Two layers
  * `Isa14Manifest` is the exact ISA14 Skill Manifest shape (five blocks, flat
    `isatvon_definition` with `i_instructions` as one string). It is permissive where the ISA14
    reference sample is, so the verbatim sample validates and round-trips byte-identically. It is
    the FDE/LLM-facing shape and rejects `sbam_build_record` (platform-only).
  * `SkillManifest` is what SBAM writes: the ISA14 blocks plus `sbam_build_record`, with the SBAM
    rules on top: skill_type `Deterministic_Loopy_MCP` (O-15), status DRAFT/VALIDATING/APPROVED/
    SUPERSEDED, version MAJOR.0.PATCH matching `governance.versioning` (C-32), ISO-8601 UTC
    timestamps, APPROVED implies `immutable_after_approval` (C-52), and `n_notification.next`
    present (N = Next).

Rules worth knowing
  * Use `.dump()` for the JSON object form. It leaves unset optional keys out, so what went in
    comes out.
  * Hashes come from `sbam_contracts.canonical` (RFC 8785). `SkillManifest.sealed(data)` fills
    `governance.deterministic_hash`; `WorkflowManifest.sealed(data)` fills
    `integrity.content_sha256`.
  * Regenerate the committed JSON Schemas from `server/` with `python -m sbam_contracts.manifest`.
"""

import copy
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from sbam_contracts import gate_expr
from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.canonical import (
    HASH_PATTERN,
    deterministic_hash,
    workflow_content_hash,
)
from sbam_contracts.isatvon14 import (
    LOOP_PROGRESS,
    MACRO_CATEGORY,
    MAX_ITERATIONS_RANGE,
    SKILL_ID,
    ExprText,
    ExternalData,
    IsatvonDefinition,
    Library,
    Parameter,
    ShortText,
    StateHandoffFile,
    Text,
    VVariables,
    parse_budget_ms,
)

HASH_PLACEHOLDER: Final = "sha256:TO_BE_COMPUTED_ON_SAVE"  # ISA14 sample value, never on APPROVED
WORKFLOW_SCHEMA_VERSION: Final = "sbam-workflow-manifest/1.0"
VERSION = re.compile(r"^(\d+)\.0\.(\d+)$")  # MAJOR.0.PATCH (C-32)
VERSION_PATTERN: Final = r"^\d+\.0\.\d+$"  # same rule, as shown in the JSON Schema and TS
UI_UPDATE_TEMPLATE: Final = "Looping [Pass {iteration}/{max_iterations}]"
SCHEMA_DIR: Final = Path(__file__).resolve().parent.parent / "schemas" / "manifest"

Digest = Annotated[str, Field(strict=True, pattern=r"^(sha256:)?[0-9a-f]{64}$")]


class ManifestStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"  # = SBAM certified + frozen (C-52)
    SUPERSEDED = "SUPERSEDED"


class CertificationState(StrEnum):
    CERTIFIED = "CERTIFIED"
    FROZEN = "FROZEN"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    def dump(self) -> dict[str, Any]:
        """JSON object form; unset optional keys stay absent so the input round-trips."""
        return self.model_dump(mode="json", exclude_unset=True)


def _utc(value: str, where: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{where} {value!r} is not ISO-8601") from None
    if parsed.utcoffset() is None or parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{where} {value!r} must be UTC")


def _version(version: str, numbers: "Versioning", where: str) -> None:
    match = VERSION.match(version)
    if match is None:
        raise ValueError(f"{where} {version!r} is not MAJOR.0.PATCH")
    if numbers.minor != 0:
        raise ValueError("governance.versioning.minor must be 0")
    if (int(match.group(1)), int(match.group(2))) != (numbers.major, numbers.patch):
        raise ValueError(f"{where} {version!r} does not match governance.versioning")


# --- ISA14 blocks (layer 1) -----------------------------------------------------------------------


class ManifestSkillMetadata(_Strict):
    macro_category: ShortText
    skill_id: ShortText
    skill_name: ShortText
    skill_type: ShortText
    reusable: bool
    version: ShortText
    status: ShortText
    owner: ShortText
    created_at: ShortText
    updated_at: ShortText


class FlatSource(_Strict):
    required_inputs: list[ShortText] = Field(min_length=1)
    fresh_state_parameters: list[Text]
    external_data: list[ExternalData] | None = None
    ml_data: Text | None = None


class FlatAutomation(_Strict):
    orchestration_layer: ShortText
    action_step: Text
    acceptance_check: Text
    trigger_mode: ShortText | None = None
    srim_metadata: Text | None = None


class FlatTechStack(_Strict):
    execution_layer: ShortText
    action_tool: ShortText
    fixed_check_tool: ShortText
    secondary_tools: list[ShortText] | None = None
    gateway_routing: ShortText | None = None
    libraries: list[Library] | None = None


class FlatVariables(_Strict):
    max_iterations: int
    success_gate: Text  # parsed by the SBAM grammar only in the structured form (isatvon14)
    no_op_condition: Text
    escalation_condition: Text
    budget_limit: ShortText
    tables: list[ShortText] | None = None
    parameters: list[Parameter] | None = None
    secrets: list[ShortText] | None = None


class FlatOutcome(_Strict):
    json_schema: dict[str, JsonValue] = Field(min_length=1)
    state_handoff_file: StateHandoffFile | Text  # ISA14 sample: one sentence; SBAM: an object
    success_criteria: Text | None = None


class FlatNotification(_Strict):
    ui_updates: list[Text]
    database_updates: list[Text]
    alerts: list[Text]
    next: ShortText | None = None


class Isa14Definition(_Strict):
    """`isatvon_definition` as exported: ISA14's flat shape (`i_instructions` is one string)."""

    i_instructions: Text
    s_source: FlatSource
    a_automation: FlatAutomation
    t_tech_stack: FlatTechStack
    v_variables: FlatVariables
    o_outcome: FlatOutcome
    n_notification: FlatNotification


class Orchestration(_Strict):
    router: ShortText
    trigger: ShortText


class Execution(_Strict):
    engine: ShortText


class Gateway(_Strict):
    route_via: ShortText


class Persistence(_Strict):
    database: ShortText
    table: Text  # derived from V.tables
    action: ShortText


class Audit(_Strict):
    telemetry: ShortText


class RuntimeUi(_Strict):
    target: ShortText
    method: ShortText


class SrimRuntimeDefinition(_Strict):
    orchestration: Orchestration
    execution: Execution
    gateway: Gateway
    persistence: Persistence
    audit: Audit
    ui: RuntimeUi


class JsonSchemaObject(_Strict):
    type: Literal["object"]
    properties: dict[str, dict[str, JsonValue]]
    required: list[ShortText] | None = None  # absent and [] are both valid and kept as given


class ManifestContracts(_Strict):
    input_schema: JsonSchemaObject
    output_schema: JsonSchemaObject


class Lifecycle(_Strict):
    state: ShortText
    approval_required_for_change: bool
    immutable_after_approval: bool


class Versioning(_Strict):
    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(ge=0)


class Governance(_Strict):
    deterministic_hash: ShortText
    lifecycle: Lifecycle
    versioning: Versioning


class Isa14Manifest(_Strict):
    """The ISA14 Skill Manifest: exactly these five blocks."""

    skill_metadata: ManifestSkillMetadata
    isatvon_definition: Isa14Definition
    srim_runtime_definition: SrimRuntimeDefinition
    contracts: ManifestContracts
    governance: Governance


# --- sbam_build_record and SkillManifest (layer 2) ------------------------------------------------


class BuildIds(_Strict):
    tenant_id: ShortText
    project_id: ShortText
    workflow_id: ShortText
    sub_skill_id: ShortText


class Provenance(_Strict):
    sow_version_id: ShortText
    sow_sha256: Digest
    approved_at: ShortText
    approved_by: ShortText
    knowledge_version_id: ShortText
    okf_sha256: Digest
    master_snapshot_sha256: Digest
    sow_refs: list[ShortText] = Field(min_length=1)


class TemplateRef(_Strict):
    name: ShortText
    version: ShortText
    sha256: Digest


class Build(_Strict):
    template: TemplateRef
    runtime_image_digest: Digest
    libraries: list[Library]
    schema_sql_sha256: Digest


class Evidence(_Strict):
    code_attempt_id: ShortText
    bundle_sha256: Digest
    gates: list[JsonValue]
    test_run_id: ShortText
    sandbox_run_id: ShortText
    passed_cases: int = Field(ge=0)


class Certification(_Strict):
    state: Annotated[CertificationState, Field(strict=False)]  # enum from a JSON string
    certified_at: ShortText
    reused_from: ShortText | None = None  # unchanged skill reuses an earlier certification


class SbamBuildRecord(_Strict):
    """Platform-only provenance and build evidence. Never accepted from the FDE or an LLM."""

    isatvon_framework_version: Literal["1.4"]
    isatvon_detail: IsatvonDefinition
    ids: BuildIds
    provenance: Provenance
    build: Build
    evidence: Evidence | None = None  # excluded from the hash
    certification: Certification | None = None  # excluded from the hash


class SbamSkillMetadata(ManifestSkillMetadata):
    """`skill_metadata` as SBAM writes it; ids, version and enums are visible in the JSON Schema."""

    macro_category: Annotated[str, Field(strict=True, pattern=MACRO_CATEGORY.pattern)]
    skill_id: Annotated[str, Field(strict=True, pattern=SKILL_ID.pattern)]
    skill_type: Literal["Deterministic_Loopy_MCP"]  # O-15
    version: Annotated[str, Field(strict=True, pattern=VERSION_PATTERN)]  # MAJOR.0.PATCH (C-32)
    status: Annotated[ManifestStatus, Field(strict=False)]  # enum from a JSON string


class SkillManifest(Isa14Manifest):
    """The Sub-Skill manifest SBAM writes: ISA14 blocks + `sbam_build_record` (TD §11.1)."""

    skill_metadata: SbamSkillMetadata
    sbam_build_record: SbamBuildRecord

    @model_validator(mode="after")
    def _sbam_rules(self) -> Self:
        meta, gov = self.skill_metadata, self.governance
        statuses = {s.value for s in ManifestStatus}
        if gov.lifecycle.state not in statuses:
            raise ValueError(
                f"lifecycle.state {gov.lifecycle.state!r} is not one of {sorted(statuses)}"
            )
        if meta.status != gov.lifecycle.state:
            raise ValueError("skill_metadata.status must equal governance.lifecycle.state (C-52)")
        _version(meta.version, gov.versioning, "skill_metadata.version")
        _utc(meta.created_at, "skill_metadata.created_at")
        _utc(meta.updated_at, "skill_metadata.updated_at")
        approved = meta.status == ManifestStatus.APPROVED
        if approved and not gov.lifecycle.immutable_after_approval:
            raise ValueError(
                "APPROVED requires governance.lifecycle.immutable_after_approval (C-52)"
            )
        placeholder_ok = gov.deterministic_hash == HASH_PLACEHOLDER and not approved
        if not (placeholder_ok or HASH_PATTERN.match(gov.deterministic_hash)):
            raise ValueError("governance.deterministic_hash must be 'sha256:' + 64 lowercase hex")
        skill = SKILL_ID.match(meta.skill_id)
        macro = MACRO_CATEGORY.match(meta.macro_category)
        if skill is None or macro is None or skill.group(1) != macro.group(1):
            raise ValueError("skill_id must be MSnn.m_<Name> under its macro_category MSnn_<Name>")
        if "next" not in self.isatvon_definition.n_notification.model_fields_set:
            raise ValueError("isatvon_definition.n_notification.next is required (null at the end)")
        return self

    def computed_hash(self) -> str:
        return deterministic_hash(self.dump())

    @classmethod
    def sealed(cls, data: dict[str, Any]) -> "SkillManifest":
        """Validate `data` with `governance.deterministic_hash` computed from it."""
        data = copy.deepcopy(data)
        data["governance"]["deterministic_hash"] = deterministic_hash(data)
        return cls.model_validate(data)


# --- Workflow manifest ----------------------------------------------------------------------------


class WorkflowIds(_Strict):
    tenant_id: ShortText
    project_id: ShortText
    workflow_id: ShortText


class WorkflowSkill(_Strict):
    skill_id: ShortText
    deterministic_hash: Annotated[str, Field(strict=True, pattern=HASH_PATTERN.pattern)]
    manifest_sha256: Digest
    # null only in a draft (E9.5): no bundle before codegen, no certification before E14
    bundle_sha256: Digest | None
    certification_id: ShortText | None


class WorkflowAgent(_Strict):
    """One Agent (TD: Macro Skill) of the Workflow and its Sub-Skills, certified once frozen."""

    macro_category: ShortText
    name: ShortText
    agent_ref: ShortText | None = None
    certification_id: ShortText | None
    skills: list[WorkflowSkill] = Field(min_length=1)


class SchemaSql(_Strict):
    artifact_key: ShortText
    sha256: Digest


class Dag(_Strict):
    nodes: list[dict[str, JsonValue]]
    edges: list[dict[str, JsonValue]]


class EvidenceEntry(_Strict):
    level: ShortText
    subject: ShortText
    certification_id: ShortText
    sha256: Digest


class WorkflowCertification(_Strict):
    state: Annotated[CertificationState, Field(strict=False)]  # enum from a JSON string
    certified_at: ShortText
    frozen_at: ShortText | None = None


class Integrity(_Strict):
    content_sha256: Annotated[str, Field(strict=True, pattern=HASH_PATTERN.pattern)]


class WorkflowManifest(_Strict):
    """The final SBAM output for one Workflow (TD §11.3). Without `certification` it is the build's
    draft (E9.5): bundle and certification ids may still be null. Once certified, none may be."""

    schema_version: Literal["sbam-workflow-manifest/1.0"]
    ids: WorkflowIds
    name: ShortText
    objective: Text
    version: ShortText
    previous_version: ShortText | None = None
    provenance: Provenance
    isatvon_framework_version: Literal["1.4"]
    macro_skills: list[WorkflowAgent] = Field(min_length=1)
    schema_sql: SchemaSql
    dag: Dag
    runtime_image_digest: Digest
    evidence_index: list[EvidenceEntry] = Field(default_factory=list)
    certification: WorkflowCertification | None = None
    integrity: Integrity

    @model_validator(mode="after")
    def _versions(self) -> Self:
        for where, version in (
            ("version", self.version),
            ("previous_version", self.previous_version),
        ):
            if version is not None and VERSION.match(version) is None:
                raise ValueError(f"{where} {version!r} is not MAJOR.0.PATCH")
        return self

    @model_validator(mode="after")
    def _certified_is_complete(self) -> Self:
        if self.certification is None:
            return self
        for agent in self.macro_skills:
            if agent.certification_id is None:
                raise ValueError(f"{agent.macro_category}: certified without a certification_id")
            for skill in agent.skills:
                if skill.bundle_sha256 is None or skill.certification_id is None:
                    raise ValueError(f"{skill.skill_id}: certified without bundle or certificate")
        return self

    def computed_hash(self) -> str:
        return workflow_content_hash(self.dump())

    @classmethod
    def sealed(cls, data: dict[str, Any]) -> "WorkflowManifest":
        """Validate `data` with `integrity.content_sha256` computed from it."""
        data = copy.deepcopy(data)
        data["integrity"] = {"content_sha256": workflow_content_hash(data)}
        return cls.model_validate(data)


# --- loop_spec ------------------------------------------------------------------------------------


class LoopSpec(_Strict):
    """`loop_spec.json`: the five loop bounds in runtime form (the full compiler is #102)."""

    max_iterations: int = Field(ge=MAX_ITERATIONS_RANGE[0], le=MAX_ITERATIONS_RANGE[1])
    success_gate: ExprText
    no_op_condition: ExprText
    escalation_condition: ExprText
    budget_limit_ms: int = Field(gt=0)
    ui_update_template: Text

    @classmethod
    def from_variables(cls, v: VVariables, ui_updates: Sequence[str]) -> "LoopSpec":
        """Build from a structured V block. Gates must parse in the SBAM grammar."""
        for gate in (v.success_gate, v.no_op_condition, v.escalation_condition):
            gate_expr.parse(gate)
        progress = next((m for m in ui_updates if LOOP_PROGRESS.search(m)), None)
        template = (
            LOOP_PROGRESS.sub(lambda _: UI_UPDATE_TEMPLATE, progress, count=1)
            if progress
            else UI_UPDATE_TEMPLATE
        )
        return cls(
            max_iterations=v.max_iterations,
            success_gate=v.success_gate,
            no_op_condition=v.no_op_condition,
            escalation_condition=v.escalation_condition,
            budget_limit_ms=parse_budget_ms(v.budget_limit),
            ui_update_template=template,
        )


# --- JSON Schemas ---------------------------------------------------------------------------------


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/manifest/`."""
    return {
        "isa14_manifest": contract_schema(Isa14Manifest, "manifest/isa14_manifest"),
        "skill_manifest": contract_schema(SkillManifest, "manifest/skill_manifest"),
        "workflow_manifest": contract_schema(WorkflowManifest, "manifest/workflow_manifest"),
        "loop_spec": contract_schema(LoopSpec, "manifest/loop_spec"),
    }


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
