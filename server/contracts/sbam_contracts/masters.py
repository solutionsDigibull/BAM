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
  * Tooling (Manufacturing Masters v0.2, issue E3.2) is the 6th master. Its rows are labelled with
    their stable id (`TOOL-MFG-01`) and it has its own Status column instead of a Misc fragment: a
    Status that starts with a MasterStatus word sets it, anything else (e.g. "Category recorded;
    configuration TBD") means PROPOSED.
  * Regenerate the committed JSON Schemas from `server/` with `python -m sbam_contracts.masters`.
"""

import json
import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sbam_contracts._jsonschema import contract_schema

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
    TOOLING = "TOOLING"  # Manufacturing Masters v0.2 (E3.2)
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


class ToolingPayload(_Master):
    """Tooling master: add-on tool categories and their bindings (Manufacturing Masters v0.2)."""

    category: Text = ""
    examples: Text = ""
    links_path: Text = ""  # pointer only, never fetched
    owner_access: Text = ""
    # The workbook column "Status" (record and deployment status). Named record_status so the
    # payload never carries SBAM's own `status` metadata; see `extract_tooling_status`.
    record_status: Text = ""
    source_version_checked_at: Text = ""
    search_tag: Text = ""
    tenant_account_scope: Text = ""
    entity_node_type: Text = ""
    security_access_level: Text = ""
    refresh_frequency: Text = ""
    parent_entity: Text = ""
    source_evidence_ref: Text = ""
    binding_review_state: Text = ""
    search_aliases_category: Text = ""


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


MasterPayload = (
    OrganizationPayload
    | RulesPayload
    | AgentsPayload
    | SkillsPayload
    | ToolingPayload
    | FiletypePayload
)


class ReadmeTab(_Master):
    """The README tab: help text. Only its structure is checked, its content is not interpreted."""

    title: Text = Field(pattern=r"^BuLLM - Admin Masters \(BAM\)")
    rows: list[Annotated[list[Text | None], Field(max_length=README_MAX_COLUMNS)]]


# --- Extraction rules -----------------------------------------------------------------------

_RULE_ID = re.compile(r"^\s*(RULE-[A-Z0-9][A-Z0-9-]*)\s*:", re.IGNORECASE)
_AGENT_ID = re.compile(r"^\s*(AG-[A-Z0-9][A-Z0-9-]*)(?![A-Z0-9-])", re.IGNORECASE)
_SKILL_ID = re.compile(r"^\s*(SK-[A-Z0-9][A-Z0-9-]*)\s*:", re.IGNORECASE)
_TEMPLATE_ID = re.compile(r"^(ORG|RULE|AG|SK|TOOL)-")
_TOOLING_STATUS = re.compile(r"^\s*([A-Za-z]+)\b")
_STATUS_FRAGMENT_START = re.compile(r"(?:^|[;\n]|\.\s)\s*Status:", re.IGNORECASE)
_STATUS_TRAILING = re.compile(r"(?:^|[\s;])Status:\s*(\S+?)\.?\s*$", re.IGNORECASE)
_SAMPLE_ROW_NUMBER = 1
_EDITABLE = frozenset(
    {MasterType.ORGANIZATION, MasterType.RULES, MasterType.AGENTS, MasterType.SKILLS}
)
# Admins create and version these in the portal; Filetype (and README) come only from the
# super-admin workbook (O-23).
ADMIN_EDITABLE: Final = _EDITABLE | {MasterType.TOOLING}


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


def extract_tooling_status(status: str) -> MasterStatus:
    """Tooling's own Status column: a leading MasterStatus word, else PROPOSED. Never raises."""
    match = _TOOLING_STATUS.match(status)
    if match:
        try:
            return MasterStatus(match.group(1).upper())
        except ValueError:
            pass
    return MasterStatus.PROPOSED


# --- Status write-back (E3.5) ---------------------------------------------------------------

# The trailing fragment, split so only the value is swapped: (keep)(Status:<spaces>)(value)(.?<ws>)
_STATUS_WRITE = re.compile(r"((?:^|[\s;])Status:\s*)(\S+?)(\.?\s*)$", re.IGNORECASE)


def status_word(status: MasterStatus) -> str:
    """How a status is written in a cell: `Approved`, as in the workbook."""
    return status.value.capitalize()


def replace_status(misc: str, status: MasterStatus) -> str:
    """Misc with its status set to `status`; every other character stays exactly as it was.

    * a trailing `Status: X` fragment: only `X` is replaced (its spacing and final dot kept);
    * an empty cell becomes `Status: <X>`;
    * text without a fragment gets `; Status: <X>` appended (` Status: <X>` after a full stop);
    * an ambiguous cell (two fragments) raises, like `extract_status`.
    The result always reads back as `status`.
    """
    extract_status(misc)  # raises on two fragments or an unknown value already in the cell
    word = status_word(status)
    match = _STATUS_WRITE.search(misc)
    if match:
        written = misc[: match.start(2)] + word + misc[match.end(2) :]
    elif not misc.strip():
        written = f"Status: {word}"
    else:
        body = misc.rstrip()
        sep = " " if body.endswith((".", ";")) else "; "
        written = f"{body}{sep}Status: {word}{misc[len(body):]}"
    if extract_status(written) is not status:
        raise ValueError("Misc can't carry this status unambiguously; edit the cell instead")
    return written


def replace_tooling_status(record_status: str, status: MasterStatus) -> str:
    """Tooling's Status column: swap a leading status word, else put `<X>; ` in front."""
    word = status_word(status)
    match = _TOOLING_STATUS.match(record_status)
    if match and match.group(1).upper() in MasterStatus.__members__:
        written = record_status[: match.start(1)] + word + record_status[match.end(1) :]
    elif not record_status.strip():
        written = word
    else:
        written = f"{word}; {record_status}"
    return written


def with_status(payload: MasterPayload, status: MasterStatus) -> MasterPayload:
    """The payload with its status written back where the workbook keeps it (E3.5)."""
    if isinstance(payload, OrganizationPayload):
        return payload.model_copy(update={"misc_audit": replace_status(payload.misc_audit, status)})
    if isinstance(payload, RulesPayload | AgentsPayload | SkillsPayload):
        return payload.model_copy(update={"misc": replace_status(payload.misc, status)})
    if isinstance(payload, ToolingPayload):
        return payload.model_copy(
            update={"record_status": replace_tooling_status(payload.record_status, status)}
        )
    raise ValueError("Filetype rows carry no status")


def _row_number(row_label: str) -> int | None:
    match = re.search(r"#(\d+)\s*$", row_label)
    return int(match.group(1)) if match else None


def extract_stable_id(row_label: str, payload: MasterPayload) -> str:
    """Stable id from the row: Organization `code`, or a `RULE-x:` / `AG-x` / `SK-x:` prefix.

    Matching ignores case and surrounding spaces, and the id is returned upper-cased
    (`rule-bom:` -> `RULE-BOM`). Rows without an id (the BAM 0.2 sample rows) fall back to their
    trimmed row label, e.g. `Agent #1`. Filetype and Tooling rows are identified by their label.
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
    elif isinstance(payload, ToolingPayload):
        status = extract_tooling_status(payload.record_status)
    else:
        status = extract_status(misc_text(payload))
    return MasterIdentity(stable_id=stable_id, status=status, source_kind=kind)


def security_access_level(payload: MasterPayload) -> str:
    """The Security / Access Level of an entry: Tooling's column or the RAG Reference key."""
    if isinstance(payload, ToolingPayload):
        return payload.security_access_level
    rag = getattr(payload, "rag_reference", None)
    return rag.security_access_level if isinstance(rag, RagReference) else ""


def is_restricted(payload: MasterPayload) -> bool:
    """Restricted entries never reach an authoritative snapshot (E3.2)."""
    return "restricted" in security_access_level(payload).lower()


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
        "readme": contract_schema(ReadmeTab, "masters/readme"),
        "organization": contract_schema(OrganizationPayload, "masters/organization"),
        "rules": contract_schema(RulesPayload, "masters/rules"),
        "agents": contract_schema(AgentsPayload, "masters/agents"),
        "skills": contract_schema(SkillsPayload, "masters/skills"),
        "tooling": contract_schema(ToolingPayload, "masters/tooling"),
        "filetype": contract_schema(FiletypePayload, "masters/filetype"),
        "rag_reference": contract_schema(RagReference, "masters/rag_reference"),
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
