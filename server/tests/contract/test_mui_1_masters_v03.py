"""MUI-1 (#780): Masters 0.3 contracts follow the manager-approved Admin Masters UI (ADR 0029)."""

import pytest
from pydantic import ValidationError
from sbam_contracts.masters import MasterStatus, MasterType
from sbam_contracts.masters_v03 import (
    COLUMNS,
    LIST_DEFAULT_HEADERS,
    MAX_MICRO_PROMPTS,
    PAYLOAD_MODELS,
    AgentKind,
    AgentsPayloadV03,
    MasterBulkRequest,
    MasterList,
    MasterListKey,
    MicroPrompt,
    OrganizationPayloadV03,
    RulePrompts,
    RulesPayloadV03,
    SkillsPayloadV03,
    ToolingPayloadV03,
    id_prefix,
    rule_org_ids,
    rule_skill_refs,
    stable_id,
    status_of,
)
from sbam_contracts.registry import ROOTS

# The approved workbook's headers (Admin_Masters/src/workbook.json, commit 40661cd), verbatim.
APPROVED_HEADERS = {
    MasterType.ORGANIZATION: [
        "Org ID", "Company / Type", "Brand Properties", "Location", "Certifications",
        "Company Segment / Niche / Offerings", "Customers / Audience", "Employees / Job Role",
        "Data Sources", "Systems / tools (Software)", "Assets (Hardware)", "FDE Notes",
        "Documents", "Artifacts",
    ],
    MasterType.RULES: [
        "Rule ID", "Rule / Type / Description", "Source / approval / evidence",
        "Org / Skills mapping + enforcement / gate", "Stop / review condition",
        "Owner / version", "Status", "Effective / expiry", "Skill ref",
    ],
    MasterType.AGENTS: [
        "Agent ID", "Agent Name", "Kind", "Type", "Reference", "Source", "Tools", "Model",
    ],
    MasterType.SKILLS: [
        "Skill ID", "Skill", "I · Instruction", "S · Source data", "A · Automation",
        "T · Tech stack", "V · Variables", "O · Outcome", "N · Next", "References",
    ],
    MasterType.TOOLING: [
        "Tooling ID", "Tool Name", "Category", "Links / Path", "Owner / access", "Status",
        "Source version / checked_at", "Refresh Frequency", "Record type", "Source tab",
        "BuLLM badge",
    ],
}  # fmt: skip


@pytest.mark.parametrize("master", list(APPROVED_HEADERS))
def test_mui_1_t01_columns_match_the_approved_headers_in_order(master: MasterType) -> None:
    columns = COLUMNS[master]
    assert [header for _, header, _ in columns] == APPROVED_HEADERS[master]
    # every workbook column is a payload field, in the same order
    fields = list(PAYLOAD_MODELS[master].model_fields)
    assert fields[: len(columns)] == [key for key, _, _ in columns]


def test_mui_1_t02_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillsPayloadV03.model_validate({"skill": "Quote", "rfq_inquiries": "old 0.2 column"})
    with pytest.raises(ValidationError):
        OrganizationPayloadV03.model_validate({"org_id": 7})  # strict text


def test_mui_1_t03_agent_kind() -> None:
    assert AgentsPayloadV03().kind == AgentKind.AGENT
    assistant = AgentsPayloadV03.model_validate({"agent_id": "", "kind": "Assistant"})
    assert assistant.kind == AgentKind.ASSISTANT
    assert id_prefix(MasterType.AGENTS, assistant) == "AS-"
    assert id_prefix(MasterType.AGENTS, AgentsPayloadV03()) == "AG-"
    with pytest.raises(ValidationError):
        AgentsPayloadV03.model_validate({"kind": "Bot"})


def test_mui_1_t04_rule_prompts_are_bounded() -> None:
    rule = RulesPayloadV03.model_validate(
        {"rule_id": "Rules-2", "prompts": {"system": "Gate", "micro": [{"name": "a", "text": "b"}]}}
    )
    assert rule.prompts.micro == [MicroPrompt(name="a", text="b")]
    with pytest.raises(ValidationError):
        RulePrompts.model_validate({"micro": [{"name": "x"}] * (MAX_MICRO_PROMPTS + 1)})


def test_mui_1_t05_master_list_is_rectangular() -> None:
    headers = list(LIST_DEFAULT_HEADERS[MasterListKey.EMPLOYEES])
    MasterList(headers=headers, rows=[["Asha", "QA lead", "Quality", "ORG-1", "Verified"]])
    with pytest.raises(ValidationError, match="row 1 has 2 cells"):
        MasterList(headers=headers, rows=[["Asha", "QA lead"]])
    with pytest.raises(ValidationError):
        MasterList(headers=["  "], rows=[])
    assert set(LIST_DEFAULT_HEADERS) == set(MasterListKey)


def test_mui_1_t06_stable_id_status_and_links() -> None:
    assert stable_id(MasterType.SKILLS, SkillsPayloadV03(skill_id=" sk-07 ")) == "SK-07"
    assert stable_id(MasterType.SKILLS, SkillsPayloadV03()) is None
    assert stable_id(MasterType.TOOLING, ToolingPayloadV03(tooling_id="TF-01")) == "TF-01"
    assert id_prefix(MasterType.RULES, RulesPayloadV03()) == "RULE-CUSTOM-"

    assert status_of(RulesPayloadV03(record_status="Proposed starter; not approved")) == (
        MasterStatus.PROPOSED
    )
    assert status_of(RulesPayloadV03(record_status="Active")) == MasterStatus.APPROVED
    assert status_of(ToolingPayloadV03(record_status="Archived")) == MasterStatus.RETIRED
    assert status_of(SkillsPayloadV03()) == MasterStatus.PROPOSED

    rule = RulesPayloadV03(
        mapping_enforcement="Org: ORG-BU-01/02; ORG-HQ. Skills: SK-01 gate",
        skill_ref="SK-01; SK-02.Quote",
    )
    assert rule_org_ids(rule) == ["ORG-BU-01", "ORG-BU-02", "ORG-HQ"]
    assert rule_skill_refs(rule) == ["SK-01", "SK-02.Quote"]


def test_mui_1_t07_new_models_are_contract_roots() -> None:
    roots = dict(ROOTS)
    for stem in ("organization", "rules", "agents", "skills", "tooling", "master_list"):
        assert f"masters_v03/{stem}" in roots
    assert roots["masters_v03/bulk_request"] is MasterBulkRequest
    with pytest.raises(ValidationError):
        MasterBulkRequest(mode="upsert", rows=[])
