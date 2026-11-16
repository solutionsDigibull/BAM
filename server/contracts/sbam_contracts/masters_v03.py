"""Admin masters contract, "Masters 0.3": the manager-approved Admin Masters UI (ADR 0029, #780).

The column sets are the approved workbook's own (`Admin_Masters/src/workbook.json`), in its order,
with its row-3 descriptions. This module is additive: BAM 0.2 (`sbam_contracts.masters`) stays the
stored layout until #781 migrates the payloads and switches the importer, exporter and service.

Rules worth knowing
  * Payload keys are the approved headers in snake case. `COLUMNS` maps each key to its exact
    header and description, so the client shows the header text and tooltip verbatim.
  * Each master has an id column (`Org ID`, `Rule ID`, ...). It is the stable id, upper-cased. A
    blank id means the server assigns the next one with `id_prefix` (#782).
  * Rules carry editor fields that are not workbook columns: `applies_to`, `fde_use`, `client_use`,
    `acts_on` and `prompts` (one system prompt, a bounded list of micro prompts).
  * Rule-to-organisation and rule-to-skill links are parsed from the rule's own cells
    (`rule_org_ids`, `rule_skill_refs`), exactly as the approved UI reads them, so there is one
    source of truth and nothing to keep in sync.
  * Status lives in a workbook column on Rules and Tools only (`record_status`); its leading word
    maps onto `MasterStatus` (`status_of`). The other masters default to PROPOSED.
  * Template lists (Employees, Customers, Assets, Systems) are not masters: a list is replaced as
    a whole by an upload and never lands in a master tab.
"""

import re
from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.masters import MasterStatus, MasterType, Text

STRUCTURE_VERSION: Final = "Masters 0.3"
MAX_MICRO_PROMPTS: Final = 50
MAX_LIST_COLUMNS: Final = 64
MAX_LIST_ROWS: Final = 20_000
MAX_BULK_ROWS: Final = 5_000

Short = Annotated[str, Field(strict=True, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_extra={"structure_version": STRUCTURE_VERSION}
    )


class OrganizationPayloadV03(_Strict):
    org_id: Text = ""
    company_type: Text = ""
    brand_properties: Text = ""
    location: Text = ""
    certifications: Text = ""
    segment_offerings: Text = ""
    customers_audience: Text = ""
    employees_roles: Text = ""
    data_sources: Text = ""  # links only; the data stays in the data lake
    systems_tools: Text = ""
    assets: Text = ""
    fde_notes: Text = ""
    documents: Text = ""
    artifacts: Text = ""


class MicroPrompt(_Strict):
    name: Short = ""
    text: Text = ""


class RulePrompts(_Strict):
    system: Text = ""
    micro: Annotated[list[MicroPrompt], Field(max_length=MAX_MICRO_PROMPTS)] = []


class RulesPayloadV03(_Strict):
    rule_id: Text = ""
    rule_type_description: Text = ""
    source_evidence: Text = ""
    mapping_enforcement: Text = ""  # "Org: ORG-1; Skills: SK-01 ..." joins to the live tabs
    stop_review_condition: Text = ""
    owner_version: Text = ""
    record_status: Text = ""
    effective_expiry: Text = ""
    skill_ref: Text = ""
    # Rules Engine editor fields (not workbook columns)
    applies_to: Text = ""
    fde_use: Text = ""
    client_use: Text = ""
    acts_on: Text = ""
    prompts: RulePrompts = RulePrompts()


class AgentKind(StrEnum):
    AGENT = "Agent"
    ASSISTANT = "Assistant"


class AgentsPayloadV03(_Strict):
    agent_id: Text = ""
    agent_name: Text = ""
    kind: AgentKind = AgentKind.AGENT
    type: Text = ""  # Proposed / Internal / External, kept as written
    reference: Text = ""  # the agent it enhances, or a registry link / path (ADR 0028 reads URLs)
    source: Text = ""  # "Built in BuLLM" or "Imported · <path>"
    tools: Text = ""
    model: Text = ""


class SkillsPayloadV03(_Strict):
    skill_id: Text = ""
    skill: Text = ""
    instruction: Text = ""
    source_data: Text = ""
    automation: Text = ""
    tech_stack: Text = ""
    variables: Text = ""
    outcome: Text = ""
    next: Text = ""
    references: Text = ""


class ToolingPayloadV03(_Strict):
    tooling_id: Text = ""
    tool_name: Text = ""
    category: Text = ""
    links_path: Text = ""  # pointer only, never fetched (ADR 0028 reads it)
    owner_access: Text = ""
    record_status: Text = ""
    source_version_checked_at: Text = ""
    refresh_frequency: Text = ""
    record_type: Text = ""
    source_tab: Text = ""
    bullm_badge: Text = ""


MasterPayloadV03 = (
    OrganizationPayloadV03
    | RulesPayloadV03
    | AgentsPayloadV03
    | SkillsPayloadV03
    | ToolingPayloadV03
)

PAYLOAD_MODELS: Final[dict[MasterType, type[MasterPayloadV03]]] = {
    MasterType.ORGANIZATION: OrganizationPayloadV03,
    MasterType.RULES: RulesPayloadV03,
    MasterType.AGENTS: AgentsPayloadV03,
    MasterType.SKILLS: SkillsPayloadV03,
    MasterType.TOOLING: ToolingPayloadV03,
}

# master -> (payload key, exact approved header, row-3 description), in workbook order.
COLUMNS: Final[dict[MasterType, tuple[tuple[str, str, str], ...]]] = {
    MasterType.ORGANIZATION: (
        ("org_id", "Org ID", "Stable unit or resource ID"),
        (
            "company_type",
            "Company / Type",
            "Company / business unit name and type (HQ, branch, department or resource)",
        ),
        ("brand_properties", "Brand Properties", "Website / GBP / Social Media"),
        ("location", "Location", "Registered address"),
        (
            "certifications",
            "Certifications",
            "Company/business unit certifications. Certificate on the first line; Validity (dd-mm-yyyy) on the second line.",  # noqa: E501
        ),
        (
            "segment_offerings",
            "Company Segment / Niche / Offerings",
            "Company segment/niche, context and goal priorities; approved services / offers",
        ),
        (
            "customers_audience",
            "Customers / Audience",
            "ICP / customer segments as four stacked lines: Customers, Suppliers, Audience, Other. One label per line, content after it.",  # noqa: E501
        ),
        ("employees_roles", "Employees / Job Role", "Named roles and approval owners"),
        (
            "data_sources",
            "Data Sources",
            "Governed document / source versions. One linked document per line, each opening its own file or sheet.",  # noqa: E501
        ),
        (
            "systems_tools",
            "Systems / tools (Software)",
            "Software only. Feeder: BuLLM Tech Stack & Tool Factory > Repo Library. Store canonical repo / Tooling ID, version and tenant account refs; no credentials. See README feeder note. Reference mapping, not live sync.",  # noqa: E501
        ),
        (
            "assets",
            "Assets (Hardware)",
            "Physical hardware only: asset ID, device/equipment type, assigned unit/owner and inventory/evidence ref. Legacy unclassified values retained pending review; not hardware verification.",  # noqa: E501
        ),
        ("fde_notes", "FDE Notes", "Changes, exceptions and open gaps"),
        ("documents", "Documents", ""),
        ("artifacts", "Artifacts", ""),
    ),
    MasterType.RULES: (
        ("rule_id", "Rule ID", "Stable rule key; one rule per row."),
        (
            "rule_type_description",
            "Rule / Type / Description",
            "Name, type and short definition. Types: Policy, Prompt, Estimation, Calculation, Validation, Decision/routing.",  # noqa: E501
        ),
        (
            "source_evidence",
            "Source / approval / evidence",
            "Source/version, approval and evidence reference; preserve source context.",
        ),
        (
            "mapping_enforcement",
            "Org / Skills mapping + enforcement / gate",
            "Org IDs + Skill IDs are joins to the live tabs. Mapping marked proposed is not approved applicability. State inputs, runtime point and required check together.",  # noqa: E501
        ),
        (
            "stop_review_condition",
            "Stop / review condition",
            "What happens on failure, missing data or conflict.",
        ),
        (
            "owner_version",
            "Owner / version",
            "Accountable owner + rule version; confirmed before activation.",
        ),
        (
            "record_status",
            "Status",
            "Proposed is not approved or implemented. Test cases are not executed tests.",
        ),
        (
            "effective_expiry",
            "Effective / expiry",
            "Start / expiry / review date; dates are required before activation.",
        ),
        ("skill_ref", "Skill ref", "Skill reference"),
    ),
    MasterType.AGENTS: (
        ("agent_id", "Agent ID", "Stable agent or assistant ID"),
        ("agent_name", "Agent Name", "Agent or assistant name and what it does"),
        (
            "kind",
            "Kind",
            "Agent: one defined job. Assistant: a cross-cutting helper across masters.",
        ),
        (
            "type",
            "Type",
            "Proposed agent (to be built) / Internal agent (already in the company) / Third-party or external agent",  # noqa: E501
        ),
        (
            "reference",
            "Reference",
            "Proposed agents only: the existing agent this one enhances or integrates. Assistants: what they cut across.",  # noqa: E501
        ),
        (
            "source",
            "Source",
            "Built in BuLLM, or Imported from another path (GitHub, MCP server, company path). The Imported tag shows on row hover.",  # noqa: E501
        ),
        ("tools", "Tools", "Tools the agent or assistant uses, pulled from the Tool Factory"),
        ("model", "Model", "Model the agent or assistant runs on"),
    ),
    MasterType.SKILLS: (
        (
            "skill_id",
            "Skill ID",
            "Stable skill key, assigned automatically when the row is added; not editable",
        ),
        ("skill", "Skill", "Skill name"),
        ("instruction", "I · Instruction", "Instruction: what the skill does, step by step"),
        ("source_data", "S · Source data", "Source data: the inputs the skill reads"),
        ("automation", "A · Automation", "Automation: when the skill runs - schedule or trigger"),
        (
            "tech_stack",
            "T · Tech stack",
            "Tech stack: the APIs, models and tools the skill runs on",
        ),
        ("variables", "V · Variables", "Variables: the values the skill captures and passes on"),
        ("outcome", "O · Outcome", "Outcome: what the skill produces"),
        ("next", "N · Next", "Next: the skill or step that runs next"),
        (
            "references",
            "References",
            "Linked Organisations (blue chips) and Tool Factory entries (grey TF chips). Rules live inside Organisations.",  # noqa: E501
        ),
    ),
    MasterType.TOOLING: (
        ("tooling_id", "Tooling ID", "Stable tool/category ID"),
        ("tool_name", "Tool Name", "Examples only; not configured integrations"),
        ("category", "Category", "Add-on category specified by user"),
        (
            "links_path",
            "Links / Path",
            "Confirmed URL / repo / document path; a placeholder path otherwise",
        ),
        ("owner_access", "Owner / access", "Named owner/permissions"),
        ("record_status", "Status", "Record and deployment status"),
        (
            "source_version_checked_at",
            "Source version / checked_at",
            "Registry version/date; tool version and freshness noted",
        ),
        ("refresh_frequency", "Refresh Frequency", "Source refresh cadence to confirm"),
        (
            "record_type",
            "Record type",
            "Agent, Assistant, MCP server, OSS repo, Skill, API, Framework or Enterprise system",
        ),
        ("source_tab", "Source tab", "Leaderboard, Repo Library, Skill Library or Tech Stack"),
        ("bullm_badge", "BuLLM badge", "BuLLM Recommended, BuLLM Tested, Gap or High fit"),
    ),
}

_ID_KEY: Final = {
    MasterType.ORGANIZATION: "org_id",
    MasterType.RULES: "rule_id",
    MasterType.AGENTS: "agent_id",
    MasterType.SKILLS: "skill_id",
    MasterType.TOOLING: "tooling_id",
}


def stable_id(master_type: MasterType, payload: MasterPayloadV03) -> str | None:
    """The id column, trimmed and upper-cased; None when blank (the server assigns one)."""
    value = str(getattr(payload, _ID_KEY[master_type])).strip().upper()
    return value or None


def id_prefix(master_type: MasterType, payload: MasterPayloadV03) -> str:
    """Prefix of a server-assigned id, as the approved UI numbers new rows."""
    if isinstance(payload, AgentsPayloadV03):
        return "AS-" if payload.kind == AgentKind.ASSISTANT else "AG-"
    return {
        MasterType.ORGANIZATION: "ORG-",
        MasterType.RULES: "RULE-CUSTOM-",
        MasterType.SKILLS: "SK-",
        MasterType.TOOLING: "TOOL-",
    }[master_type]


_STATUS_WORDS: Final = {
    "approved": MasterStatus.APPROVED,
    "active": MasterStatus.APPROVED,
    "illustrative": MasterStatus.ILLUSTRATIVE,
    "retired": MasterStatus.RETIRED,
    "archived": MasterStatus.RETIRED,
}
_LEADING_WORD: Final = re.compile(r"^\s*([A-Za-z]+)")


def status_of(payload: MasterPayloadV03) -> MasterStatus:
    """Rules and Tools: the Status column's leading word; anything else is PROPOSED."""
    text = getattr(payload, "record_status", "")
    match = _LEADING_WORD.match(text)
    return (
        _STATUS_WORDS.get(match.group(1).lower(), MasterStatus.PROPOSED)
        if match
        else (MasterStatus.PROPOSED)
    )


def _refs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,\n]+", text) if part.strip()]


def rule_skill_refs(rule: RulesPayloadV03) -> list[str]:
    """Skill ids a rule drives (`Skill ref`, `;`/`,`/newline separated). `SK-X.Y` is a subskill."""
    return _refs(rule.skill_ref)


def rule_org_ids(rule: RulesPayloadV03) -> list[str]:
    """Organisation ids named in the mapping cell, before its `Skills:` part (as the UI reads it).

    The workbook's compressed `ORG-BU-01/02` stands for ORG-BU-01 and ORG-BU-02.
    """
    mapping = re.split(r"Skills:", rule.mapping_enforcement, maxsplit=1, flags=re.IGNORECASE)[0]
    mapping = mapping.replace("ORG-BU-01/02", "ORG-BU-01; ORG-BU-02")
    tokens = re.split(r"[^A-Za-z0-9-]+", mapping)
    return list(dict.fromkeys(t for t in tokens if re.fullmatch(r"(?i)(ORG|BU)-[A-Z0-9-]+", t)))


# --- Template lists ---------------------------------------------------------------------------


class MasterListKey(StrEnum):
    EMPLOYEES = "employees"
    CUSTOMERS = "customers"
    ASSETS = "assets"
    SYSTEMS = "systems"


# Stock headers of each list (the BuLLM Recommended templates); an upload replaces them.
LIST_DEFAULT_HEADERS: Final[dict[MasterListKey, tuple[str, ...]]] = {
    MasterListKey.EMPLOYEES: (
        "Employee",
        "Job title",
        "Department",
        "Unit applicability",
        "Verification status",
    ),
    MasterListKey.CUSTOMERS: (
        "Customer ID",
        "Customer / Audience",
        "Segment / niche",
        "Region",
        "Related Org ID",
        "Owner type (Employee or Company)",
        "Owner",
        "Status",
        "Note",
    ),
    MasterListKey.ASSETS: (
        "Asset ID",
        "Asset (hardware)",
        "Type",
        "Location",
        "Org ID",
        "Owner type (Employee or Company)",
        "Owner",
        "Status",
        "Note",
    ),
    MasterListKey.SYSTEMS: (
        "System ID",
        "System / tool (software)",
        "Category",
        "Used by Org ID",
        "Owner type (Employee or Company)",
        "Owner",
        "Access level",
        "Status",
        "Note",
    ),
}

Header = Annotated[str, Field(strict=True, min_length=1, max_length=200, pattern=r"\S")]


class MasterList(BaseModel):
    """A whole template list (`PUT /admin/master-lists/{key}` replaces it, #782)."""

    model_config = ConfigDict(extra="forbid")

    headers: Annotated[list[Header], Field(min_length=1, max_length=MAX_LIST_COLUMNS)]
    rows: Annotated[list[list[Text]], Field(max_length=MAX_LIST_ROWS)] = []

    @model_validator(mode="after")
    def _rectangular(self) -> "MasterList":
        width = len(self.headers)
        for index, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"row {index + 1} has {len(row)} cells; expected {width}")
        return self


# --- Bulk rows (Add files, Import agents, Skills import) --------------------------------------


class MasterBulkRequest(BaseModel):
    """Rows the client already mapped onto one master's columns (`POST .../bulk`, #782).

    `validate-only` writes nothing. `upsert` adds new rows; an exact repeat is skipped and a row
    whose id exists with different values is reported, never overwritten.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["validate-only", "upsert"]
    rows: Annotated[list[dict[str, str]], Field(min_length=1, max_length=MAX_BULK_ROWS)]


class BulkRowError(BaseModel):
    row: int = Field(ge=1)
    field: str | None = None
    code: str
    message: str


class BulkConflict(BaseModel):
    row: int = Field(ge=1)
    stable_id: str
    fields: list[str]  # payload keys whose stored value differs


class MasterBulkReport(BaseModel):
    master_type: MasterType
    mode: Literal["validate-only", "upsert"]
    applied: bool
    created: list[str]  # stable ids (assigned ones included)
    skipped_exact: int = Field(ge=0)
    conflicts: list[BulkConflict]
    errors: list[BulkRowError]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/masters_v03/`."""
    return {
        stem: contract_schema(model, f"masters_v03/{stem}")
        for stem, model in (
            ("organization", OrganizationPayloadV03),
            ("rules", RulesPayloadV03),
            ("agents", AgentsPayloadV03),
            ("skills", SkillsPayloadV03),
            ("tooling", ToolingPayloadV03),
            ("master_list", MasterList),
            ("bulk_request", MasterBulkRequest),
            ("bulk_report", MasterBulkReport),
        )
    }
