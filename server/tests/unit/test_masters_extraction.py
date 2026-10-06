"""E0.4 unit tests: stable id, status and source kind, enum limits, RAG Reference, formulas."""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.masters import (
    AgentsPayload,
    FiletypePayload,
    MasterStatus,
    MasterType,
    OrganizationPayload,
    RagReference,
    RulesPayload,
    SkillsPayload,
    SourceKind,
    extract_stable_id,
    extract_status,
    identify,
    is_formula_like,
)
from sbam_contracts.masters_workbook import parse_rag_cell

RAG: dict[str, str] = {
    "Example": "PCBA Costing Standard",
    "Links / Path": "s3://domain-rules/pcba_costing_v1.json",
    "Entity / Node Type": "DomainKnowledge",
    "Security / Access Level": "Internal",
    "Refresh Frequency": "Quarterly",
    "ID Field": "DOM-PCBA-01",
    "Parent Entity": "ORG-001",
}


def filetype(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "file_format": ".md",
        "tier": 1,
        "purpose": "Notes",
        "rag_ingestion": "Cognee TextLoader",
        "example": "https://example.com/sample.md",
    }
    return base | overrides


# --- E0.4-T02: stable id extraction ----------------------------------------------------------


def test_e0_4_t02_rules_prefix() -> None:
    payload = RulesPayload(domain_rule="RULE-BOM: applies to BOM scrub. Policy: compare")
    assert extract_stable_id("Rule #2", payload) == "RULE-BOM"


def test_e0_4_t02_agents_prefix() -> None:
    payload = AgentsPayload(description="AG-INTAKE. Allowed input: RFQ. Required output: BOM")
    assert extract_stable_id("Agent #2", payload) == "AG-INTAKE"


def test_e0_4_t02_skills_prefix() -> None:
    assert extract_stable_id("Skill #3", SkillsPayload(skill="SK-BOM: scrub")) == "SK-BOM"


def test_e0_4_t02_organization_code() -> None:
    assert extract_stable_id("Unit #2", OrganizationPayload(code="ORG-EMS")) == "ORG-EMS"


def test_e0_4_t02_prefix_matching_ignores_case_and_surrounding_spaces() -> None:
    payload = RulesPayload(domain_rule="  rule-bom : applies to")
    assert extract_stable_id("Rule #2", payload) == "RULE-BOM"
    assert extract_stable_id("Unit #2 ", OrganizationPayload(code=" ORG-EMS ")) == "ORG-EMS"


# --- E0.4-T03: fallback id for the BAM 0.2 sample rows -----------------------------------------


def test_e0_4_t03_sample_agent_falls_back_to_row_label() -> None:
    payload = AgentsPayload(agent_name="BOM Parser", description="Parses BOM files")
    identity = identify(MasterType.AGENTS, "Agent #1", payload)
    assert identity.stable_id == "Agent #1"
    assert identity.source_kind == SourceKind.BAM_0_2_SAMPLE


def test_e0_4_t03_row_label_trailing_space_is_trimmed_in_the_id() -> None:
    identity = identify(MasterType.SKILLS, "Skill #1 ", SkillsPayload(skill="RFQ Pipeline"))
    assert identity.stable_id == "Skill #1"


def test_e0_4_t03_sample_rows_are_illustrative_whatever_misc_says() -> None:
    payload = RulesPayload(domain_rule="Costing", misc="Scrap 1.5%. Status: Approved")
    identity = identify(MasterType.RULES, "Rule #1", payload)
    assert identity.status == MasterStatus.ILLUSTRATIVE


def test_e0_4_t03_template_row_and_admin_entry_kinds() -> None:
    template = identify(MasterType.SKILLS, "Skill #3", SkillsPayload(skill="SK-BOM: scrub"))
    assert template.source_kind == SourceKind.TEMPLATE_ROW
    admin = identify(MasterType.SKILLS, "Skill #9", SkillsPayload(skill="Custom step"))
    assert admin.source_kind == SourceKind.ADMIN_ENTRY
    assert admin.stable_id == "Skill #9"


def test_e0_4_t03_filetype_rows_use_their_label_and_are_template_rows() -> None:
    identity = identify(MasterType.FILETYPE, "Filetype #7 ", FiletypePayload(**filetype()))
    assert (identity.stable_id, identity.source_kind) == ("Filetype #7", SourceKind.TEMPLATE_ROW)
    assert identity.status == MasterStatus.PROPOSED


# --- E0.4-T04: status parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("misc", "expected"),
    [
        ("Status: Illustrative", MasterStatus.ILLUSTRATIVE),
        ("", MasterStatus.PROPOSED),
        ("Scrap rate 1.5%. Status: Proposed", MasterStatus.PROPOSED),
        ("Audit log v2.4; status: approved", MasterStatus.APPROVED),
        ("Notes. Status: Retired.", MasterStatus.RETIRED),
    ],
)
def test_e0_4_t04_status_values(misc: str, expected: MasterStatus) -> None:
    assert extract_status(misc) == expected


def test_e0_4_t04_unknown_status_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown status Foo"):
        extract_status("Status: Foo")


def test_e0_4_t04_two_status_fragments_are_ambiguous() -> None:
    with pytest.raises(ValueError, match="multiple Status fragments"):
        extract_status("Status: Proposed; Status: Approved")


def test_e0_4_t04_status_that_is_not_trailing_is_plain_text() -> None:
    # Skills #1 in the workbook: a mid-text fragment must never be read as the entry's status.
    misc = "Quoting Status: In-Progress, Priority: High, Due: 2026-10-15"
    assert extract_status(misc) == MasterStatus.PROPOSED
    assert extract_status("Status: Approved by Ops, pending sign-off") == MasterStatus.PROPOSED


# --- E0.4-T05: RAG Reference -------------------------------------------------------------------


def test_e0_4_t05_seven_keys_are_valid_and_empty_values_are_allowed() -> None:
    assert parse_rag_cell(json.dumps(RAG)) == RagReference.model_validate(RAG)
    empty = parse_rag_cell(json.dumps({key: "" for key in RAG}))
    assert empty is not None
    assert empty.id_field == ""


def test_e0_4_t05_six_and_eight_keys_are_invalid() -> None:
    six = {k: v for k, v in RAG.items() if k != "Parent Entity"}
    with pytest.raises(ValueError, match="7 documented keys"):
        parse_rag_cell(json.dumps(six))
    with pytest.raises(ValueError, match="7 documented keys"):
        parse_rag_cell(json.dumps(RAG | {"Extra": "x"}))


def test_e0_4_t05_array_and_broken_json_are_invalid() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_rag_cell("[1, 2]")
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_rag_cell("{not json")


def test_e0_4_t05_non_text_values_are_invalid() -> None:
    with pytest.raises(ValueError, match="7 documented keys"):
        parse_rag_cell(json.dumps(RAG | {"ID Field": 7}))


def test_e0_4_t05_blank_cell_means_no_reference() -> None:
    assert parse_rag_cell("") is None
    assert parse_rag_cell("   ") is None


# --- E0.4-T07: enum boundaries ----------------------------------------------------------------


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_e0_4_t07_filetype_tier_inside_bounds(tier: int) -> None:
    assert FiletypePayload(**filetype(tier=tier)).tier == tier


@pytest.mark.parametrize("tier", [0, 4, -1, "2", 2.5, True])
def test_e0_4_t07_filetype_tier_outside_bounds(tier: Any) -> None:
    with pytest.raises(ValidationError):
        FiletypePayload(**filetype(tier=tier))


@pytest.mark.parametrize("org_type", ["HQ", "", "Branch", "department", " BRANCH "])
def test_e0_4_t07_organization_type_accepts_enum_in_any_case_or_blank(org_type: str) -> None:
    assert OrganizationPayload(type=org_type).type == org_type  # stored as written


@pytest.mark.parametrize("org_type", ["Plant", "Headquarters", "HQ2"])
def test_e0_4_t07_organization_type_rejects_unknown(org_type: str) -> None:
    with pytest.raises(ValidationError, match="unknown organization type"):
        OrganizationPayload(type=org_type)


def test_e0_4_t07_filetype_example_must_be_a_url() -> None:
    with pytest.raises(ValidationError):
        FiletypePayload(**filetype(example="not a link"))


# --- E0.4-T08: formula injection -----------------------------------------------------------------


@pytest.mark.parametrize("text", ['=HYPERLINK("http://evil")', "+1+1", "-2+3", "@SUM(A1)"])
def test_e0_4_t08_formula_like_text_is_stored_literally(text: str) -> None:
    payload = OrganizationPayload(org_name=text)
    assert payload.org_name == text  # never evaluated, never altered
    assert is_formula_like(text)


def test_e0_4_t08_ordinary_text_is_not_formula_like() -> None:
    assert not is_formula_like("Rocketreach Pvt. Ltd.")
    assert not is_formula_like("")


# --- Payload hygiene ----------------------------------------------------------------------------


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        RulesPayload.model_validate({"domain_rule": "x", "priority": "high"})


def test_cell_over_excel_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentsPayload(description="x" * 32_768)
