"""Admin masters contract: the BAM 0.2 structure frozen on 5 Oct 2026 (TD §7.3.1, issue E0.4).

This module holds the models and the extraction rules. The workbook layout and the checks over
already-read cell grids live in `sbam_contracts.masters_workbook`.

Rules worth knowing
  * Payloads hold the workbook's own columns only. `stable_id`, `status` and `source_kind` are SBAM
    metadata kept beside the entry, never inside the payload and never written back as a
    workbook column.
  * Cell text is stored exactly as written (no stripping, no case folding) so import -> export is
    identical. Text starting with `=`, `+`, `-` or `@` is plain text and is never evaluated.
  * Status is a trailing `Status: <value>` fragment at the end of the Misc cell (MSC README). A
    fragment anywhere else is ordinary text. Missing means PROPOSED. Sample row #1 of each editable
    tab is ILLUSTRATIVE (O-24).
  * Regenerate the committed JSON Schemas from `server/` with `python -m sbam_contracts.masters`.
"""

import json
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

STRUCTURE_VERSION: Final = "BAM 0.2"
MAX_CELL_CHARS: Final = 32_767  # Excel's own per-cell limit
FORMULA_PREFIXES: Final = ("=", "+", "-", "@")
README_MAX_COLUMNS: Final = 4

Text = Annotated[str, Field(strict=True, max_length=MAX_CELL_CHARS)]


class MasterType(StrEnum):
    ORGANIZATION = "ORGANIZATION"
    RULES = "RULES"
    AGENTS = "AGENTS"
    SKILLS = "SKILLS"
    FILETYPE = "FILETYPE"  # system-owned, read-only for Admin and FDE (O-23)


class MasterStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ILLUSTRATIVE = "ILLUSTRATIVE"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class SourceKind(StrEnum):
    # Row #1 of Organization, Rules, Agents and Skills.
    BAM_0_2_SAMPLE = "BAM_0_2_SAMPLE"
    # Follows the template id convention. Also the frozen Filetype rows, carried verbatim.
    TEMPLATE_ROW = "TEMPLATE_ROW"
    # Anything else, including rows created in the Admin portal.
    ADMIN_ENTRY = "ADMIN_ENTRY"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Master(_Strict):
    model_config = ConfigDict(json_schema_extra={"structure_version": STRUCTURE_VERSION})


class RagReference(_Strict):
    """RAG Reference cell (Rules and Skills): a JSON object with exactly these 7 keys.

    Values may be empty strings. `Links / Path` is a pointer and is never fetched.
    """

    example: Text = Field(alias="Example")
    links_path: Text = Field(alias="Links / Path")
    entity_node_type: Text = Field(alias="Entity / Node Type")
    security_access_level: Text = Field(alias="Security / Access Level")
    refresh_frequency: Text = Field(alias="Refresh Frequency")
    id_field: Text = Field(alias="ID Field")
    parent_entity: Text = Field(alias="Parent Entity")


_ORG_TYPES: Final = frozenset({"HQ", "BRANCH", "DEPARTMENT"})


class OrganizationPayload(_Master):
    org_name: Text = ""
    type: Text = ""  # HQ, BRANCH or DEPARTMENT in any case (kept as written); blank allowed
    code: Text = ""  # stable id, e.g. ORG-EMS, BU-AERO-01
    location: Text = ""
    org_details: Text = ""
    employees: Text = ""
    customers: Text = ""
    offerings: Text = ""
    competencies: Text = ""
    data_sources: Text = ""  # pointers only, never file content
    systems_tools: Text = ""
    assets: Text = ""
    certifications: Text = ""
    misc_audit: Text = ""  # status lives here as a trailing "Status: <value>"

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        shown = value.strip()
        if shown and shown.upper() not in _ORG_TYPES:
            raise ValueError(f"unknown organization type {shown!r}; use HQ, BRANCH or DEPARTMENT")
        return value


class RulesPayload(_Master):
    domain_rule: Text = ""
    pcb_panelization: Text = ""
    components_bom: Text = ""
    materials_finish: Text = ""
    box_build: Text = ""
    machine_hr_rates: Text = ""
    outsourced_services: Text = ""
    assembly_routing: Text = ""
    rules_dfm_sourcing: Text = ""
    misc: Text = ""
    rag_reference: RagReference | None = None


class AgentsPayload(_Master):
    agent_name: Text = ""
    description: Text = ""
    model: Text = ""
    repo: Text = ""
    api: Text = ""
    mcp_server: Text = ""
    script: Text = ""
    prompt: Text = ""
    eval: Text = ""
    logs: Text = ""
    misc: Text = ""


class SkillsPayload(_Master):
    skill: Text = ""
    documents: Text = ""
    rfq_inquiries: Text = ""
    currency: Text = ""
    supplier_master: Text = ""
    commodity_pricing: Text = ""
    shipping_logistics: Text = ""
    templates: Text = ""
    hitl_governance: Text = ""
    misc: Text = ""
    rag_reference: RagReference | None = None


class FiletypePayload(_Master):
    """One of the 43 frozen filetypes. Changed only by super-admin through a new workbook (O-23)."""

    model_config = ConfigDict(
        json_schema_extra={"structure_version": STRUCTURE_VERSION, "x-system-owned": True}
    )

    file_format: Text = Field(min_length=1)
    tier: Annotated[int, Field(strict=True, ge=1, le=3)]
    purpose: Text
    rag_ingestion: Text
    example: Text = Field(pattern=r"^https?://\S+$")


MasterPayload = OrganizationPayload | RulesPayload | AgentsPayload | SkillsPayload | FiletypePayload


class ReadmeTab(_Master):
    """The README tab: help text. Only its structure is checked, its content is not interpreted."""

    title: Text = Field(pattern=r"^BuLLM - Admin Masters \(BAM\)")
    rows: list[Annotated[list[Text | None], Field(max_length=README_MAX_COLUMNS)]]


# --- Extraction rules -----------------------------------------------------------------------

_RULE_ID = re.compile(r"^\s*(RULE-[A-Z0-9][A-Z0-9-]*)\s*:", re.IGNORECASE)
_AGENT_ID = re.compile(r"^\s*(AG-[A-Z0-9][A-Z0-9-]*)(?![A-Z0-9-])", re.IGNORECASE)
_SKILL_ID = re.compile(r"^\s*(SK-[A-Z0-9][A-Z0-9-]*)\s*:", re.IGNORECASE)
_TEMPLATE_ID = re.compile(r"^(ORG|RULE|AG|SK)-")
_STATUS_FRAGMENT_START = re.compile(r"(?:^|[;\n]|\.\s)\s*Status:", re.IGNORECASE)
_STATUS_TRAILING = re.compile(r"(?:^|[\s;])Status:\s*(\S+?)\.?\s*$", re.IGNORECASE)
_SAMPLE_ROW_NUMBER = 1
_EDITABLE = frozenset(
    {MasterType.ORGANIZATION, MasterType.RULES, MasterType.AGENTS, MasterType.SKILLS}
)


class MasterIdentity(BaseModel):
    """SBAM metadata derived from a row. Stored beside the payload, never inside it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = Field(min_length=1)
    status: MasterStatus
    source_kind: SourceKind


def extract_status(misc: str) -> MasterStatus:
    """Parse the trailing `Status: <value>` fragment of a Misc cell. Missing means PROPOSED.

    A `Status:` that is not the last thing in the cell is ordinary text, so it can never silently
    approve an entry. More than one standalone fragment is ambiguous and raises, as does an unknown
    value.
    """
    if len(_STATUS_FRAGMENT_START.findall(misc)) > 1:
        raise ValueError("multiple Status fragments in Misc")
    match = _STATUS_TRAILING.search(misc.strip())
    if match is None:
        return MasterStatus.PROPOSED
    value = match.group(1)
    try:
        return MasterStatus(value.upper())
    except ValueError:
        raise ValueError(f"unknown status {value}") from None


def _row_number(row_label: str) -> int | None:
    match = re.search(r"#(\d+)\s*$", row_label)
    return int(match.group(1)) if match else None


def extract_stable_id(row_label: str, payload: MasterPayload) -> str:
    """Stable id from the row: Organization `code`, or a `RULE-x:` / `AG-x` / `SK-x:` prefix.

    Matching ignores case and surrounding spaces, and the id is returned upper-cased
    (`rule-bom:` -> `RULE-BOM`). Rows without an id (the BAM 0.2 sample rows) fall back to their
    trimmed row label, e.g. `Agent #1`. Filetype rows are identified by their label.
    """
    fallback = row_label.strip()
    if isinstance(payload, OrganizationPayload):
        return payload.code.strip() or fallback
    if isinstance(payload, RulesPayload):
        match = _RULE_ID.match(payload.domain_rule)
    elif isinstance(payload, AgentsPayload):
        match = _AGENT_ID.match(payload.description)
    elif isinstance(payload, SkillsPayload):
        match = _SKILL_ID.match(payload.skill)
    else:
        return fallback
    return match.group(1).upper() if match else fallback


def source_kind(master_type: MasterType, row_label: str, stable_id: str) -> SourceKind:
    """Row #1 of each editable tab is the BAM 0.2 sample; template-convention ids are template rows.

    The frozen Filetype rows are carried verbatim from the shipped workbook, so they count as
    template rows.
    """
    if master_type in _EDITABLE and _row_number(row_label) == _SAMPLE_ROW_NUMBER:
        return SourceKind.BAM_0_2_SAMPLE
    if master_type == MasterType.FILETYPE or _TEMPLATE_ID.match(stable_id):
        return SourceKind.TEMPLATE_ROW
    return SourceKind.ADMIN_ENTRY


def misc_text(payload: MasterPayload) -> str:
    """The Misc cell where the status fragment lives (Filetype has none)."""
    if isinstance(payload, OrganizationPayload):
        return payload.misc_audit
    if isinstance(payload, RulesPayload | AgentsPayload | SkillsPayload):
        return payload.misc
    return ""


def identify(master_type: MasterType, row_label: str, payload: MasterPayload) -> MasterIdentity:
    """All three rules together. Sample rows are ILLUSTRATIVE whatever their Misc says (O-24)."""
    stable_id = extract_stable_id(row_label, payload)
    kind = source_kind(master_type, row_label, stable_id)
    if kind == SourceKind.BAM_0_2_SAMPLE:
        status = MasterStatus.ILLUSTRATIVE
    else:
        status = extract_status(misc_text(payload))
    return MasterIdentity(stable_id=stable_id, status=status, source_kind=kind)


# --- Cell text ------------------------------------------------------------------------------


def cell_text(value: Any) -> str:
    """Cell value as text exactly as written. Whole numbers lose the float tail (1.0 -> '1')."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def is_formula_like(text: str) -> bool:
    """True for text a spreadsheet could treat as a formula.

    Such text is kept verbatim here and must be written back as an explicit string cell.
    """
    return text.startswith(FORMULA_PREFIXES)


# --- JSON Schemas ---------------------------------------------------------------------------

SCHEMA_DIR: Final = Path(__file__).resolve().parents[1] / "schemas" / "masters"


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/masters/`."""
    return {
        "readme": ReadmeTab.model_json_schema(),
        "organization": OrganizationPayload.model_json_schema(),
        "rules": RulesPayload.model_json_schema(),
        "agents": AgentsPayload.model_json_schema(),
        "skills": SkillsPayload.model_json_schema(),
        "filetype": FiletypePayload.model_json_schema(),
        "rag_reference": RagReference.model_json_schema(),
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
