"""MUI-2 (#781): the 0.2 -> Masters 0.3 data migration loses nothing and validates as 0.3."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sbam_contracts.masters import (
    AgentsPayload,
    MasterStatus,
    MasterType,
    OrganizationPayload,
    RulesPayload,
    SkillsPayload,
    ToolingPayload,
)
from sbam_contracts.masters_v03 import PAYLOAD_MODELS, is_restricted, status_of

MIGRATION = (
    Path(__file__).resolve().parents[2] / "api/sbam_api/migrations/versions/0056_masters_0_3.py"
)
OLD_MODELS = {
    "ORGANIZATION": OrganizationPayload,
    "RULES": RulesPayload,
    "AGENTS": AgentsPayload,
    "SKILLS": SkillsPayload,
    "TOOLING": ToolingPayload,
}
RAG = {
    "Example": "PCBA Costing Standard",
    "Links / Path": "s3://domain-rules/pcba.json",
    "Entity / Node Type": "DomainKnowledge",
    "Security / Access Level": "Restricted",
    "Refresh Frequency": "Quarterly",
    "ID Field": "DOM-PCBA-01",
    "Parent Entity": "ORG-EMS",
}


@pytest.fixture(name="migration", scope="module")
def fixture_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m0054", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_payload(master_type: str) -> dict[str, Any]:
    """Every 0.2 field filled with its own name, so each value can be traced."""
    values: dict[str, Any] = {f: f"v-{f}" for f in OLD_MODELS[master_type].model_fields}
    if master_type == "ORGANIZATION":
        values["type"] = "HQ"
    if "rag_reference" in values:
        values["rag_reference"] = RAG
    return OLD_MODELS[master_type].model_validate(values).model_dump(mode="json", by_alias=True)


def strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    return [value] if isinstance(value, str) else []


@pytest.mark.parametrize("master_type", list(OLD_MODELS))
def test_mui_2_t01_every_value_survives_and_the_payload_is_0_3(
    migration: ModuleType, master_type: str
) -> None:
    old = old_payload(master_type)
    new = migration.to_v03(master_type, old, "APPROVED", "Unit #1", "Unit #1")
    model = PAYLOAD_MODELS[MasterType(master_type)].model_validate(new)  # a valid 0.3 payload
    dumped = json.dumps(model.model_dump(mode="json"), ensure_ascii=False)
    for value in strings(old):
        assert value in dumped or json.dumps(value)[1:-1] in dumped, (master_type, value)
    assert new[migration.ID_KEY[master_type]] == "UNIT #1"  # stable ids are upper-cased
    assert new["legacy_0_2"]["_row_label"] == "Unit #1"


def test_mui_2_t01_the_main_columns_map_where_the_report_says(migration: ModuleType) -> None:
    org = migration.to_v03("ORGANIZATION", old_payload("ORGANIZATION"), "PROPOSED", "ORG-EMS", "x")
    assert org["company_type"] == "v-org_name - HQ" and org["fde_notes"] == "v-misc_audit"
    agent = migration.to_v03("AGENTS", old_payload("AGENTS"), "PROPOSED", "AG-1", "x")
    assert (agent["reference"], agent["kind"], agent["model"]) == ("v-repo", "Agent", "v-model")
    tool = migration.to_v03("TOOLING", old_payload("TOOLING"), "PROPOSED", "TOOL-MFG-01", "x")
    assert (tool["tool_name"], tool["record_type"]) == ("v-examples", "v-entity_node_type")


def test_mui_2_t02_rules_keep_their_status_and_restricted_stays_restricted(
    migration: ModuleType,
) -> None:
    for status in MasterStatus:
        rule = migration.to_v03("RULES", old_payload("RULES"), status.value, "RULE-BOM", "x")
        assert status_of(PAYLOAD_MODELS[MasterType.RULES].model_validate(rule)) == status
    skill = migration.to_v03("SKILLS", old_payload("SKILLS"), "PROPOSED", "SK-1", "x")
    assert is_restricted(PAYLOAD_MODELS[MasterType.SKILLS].model_validate(skill))
    plain = old_payload("SKILLS") | {"rag_reference": None}
    skill = migration.to_v03("SKILLS", plain, "PROPOSED", "SK-1", "x")
    assert not is_restricted(PAYLOAD_MODELS[MasterType.SKILLS].model_validate(skill))
