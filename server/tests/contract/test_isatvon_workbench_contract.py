"""E8.7 contract: the workbench's tree and edit shapes (#99), pending the endpoints (#94).

The MS01.1 fixture is the Sub-Skill; every violation path the validator reports must name a grid
column, so the workbench can mark the cell it belongs to.
"""

import json
import uuid
from typing import Any

import pytest
from fixtures import fixture_path
from pydantic import ValidationError
from sbam_api.isatvon import grid
from sbam_contracts import isatvon_grid
from sbam_contracts.isatvon14 import SubSkill, validate_sub_skill
from sbam_contracts.isatvon_workbench import (
    MacroSkillPatch,
    SubSkillPatch,
    ValidationReport,
    WorkbenchSubSkill,
    WorkbenchTree,
)

SHA = "a" * 64


def ms01_1() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8")
    )
    return data


def row(**over: Any) -> dict[str, Any]:
    base = {
        "id": str(uuid.uuid4()),
        "macro_id": str(uuid.uuid4()),
        "state": "VALID",
        "content_sha256": SHA,
        "validation_errors": [],
        "sub_skill": ms01_1(),
    }
    return {**base, **over}


def test_e8_7_grid_models_moved_unchanged() -> None:
    assert grid.IsatvonSchemaResponse is isatvon_grid.IsatvonSchemaResponse
    rendered = grid.render("1.4")
    assert rendered is not None
    body = isatvon_grid.IsatvonSchemaResponse.model_validate_json(rendered.body)
    assert body == rendered.payload


def test_e8_7_tree_round_trips() -> None:
    tree = WorkbenchTree.model_validate(
        {
            "workflow_id": str(uuid.uuid4()),
            "workflow_state": "ISATVON_REVIEW",
            "sow_approved": True,
            "macros": [
                {
                    "id": str(uuid.uuid4()),
                    "macro_category": "MS01",
                    "name": "Intake",
                    "description": "RFQ intake",
                    "order": 0,
                    "sub_skills": [row()],
                }
            ],
        }
    )
    assert WorkbenchTree.model_validate_json(tree.model_dump_json()) == tree
    assert isinstance(tree.macros[0].sub_skills[0].sub_skill, SubSkill)


@pytest.mark.parametrize(
    "bad",
    [
        {"content_sha256": "not-a-sha"},
        {"state": "MAYBE"},
        {"extra": 1},
        {"sub_skill": {"skill_metadata": {}}},
    ],
)
def test_e8_7_sub_skill_row_rejects_bad_rows(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WorkbenchSubSkill.model_validate(row(**bad))


def test_e8_7_violation_paths_name_grid_columns() -> None:
    raw = ms01_1()
    raw["isatvon_definition"]["v_variables"]["max_iterations"] = 11  # rule 5: range 1-10
    violations = validate_sub_skill(raw)
    assert violations, "the fixture edit must break a rule"
    rendered = grid.render("1.4")
    assert rendered is not None
    paths = {c.field_path for g in rendered.payload.grid.column_groups for c in g.children}
    for v in violations:
        assert any(
            v.path == p or v.path.startswith(f"{p}.") or v.path.startswith(f"{p}[") for p in paths
        ), v
    reported = WorkbenchSubSkill.model_validate(
        row(state="INVALID", validation_errors=[v.model_dump() for v in violations])
    )
    assert reported.validation_errors == violations


def test_e8_7_patch_takes_dotted_paths_and_an_order() -> None:
    ok = SubSkillPatch.model_validate(
        {"changes": {"isatvon_definition.v_variables.max_iterations": 3}}
    )
    assert ok.changes == {"isatvon_definition.v_variables.max_iterations": 3}
    assert SubSkillPatch.model_validate({"order": 2}).order == 2
    bads: list[dict[str, Any]] = [{}, {"changes": {}}, {"order": 0}]
    for bad in bads:
        with pytest.raises(ValidationError):
            SubSkillPatch.model_validate(bad)


def test_e8_4_patch_keeps_unknown_top_level_keys_so_the_server_can_name_them_read_only() -> None:
    patch = SubSkillPatch.model_validate({"state": "VALID"})
    assert patch.model_extra == {"state": "VALID"}


def test_e8_4_macro_patch_limits() -> None:
    assert MacroSkillPatch.model_validate({"name": "A", "order": 1}).order == 1
    bads: list[dict[str, Any]] = [
        {},
        {"name": ""},
        {"name": "x" * 121},
        {"description": "x" * 2001},
        {"order": 0},
        {"state": "X"},
    ]
    for bad in bads:
        with pytest.raises(ValidationError):
            MacroSkillPatch.model_validate(bad)


def test_e8_7_validation_report() -> None:
    report = ValidationReport.model_validate(
        {"sub_skills": [{"id": str(uuid.uuid4()), "state": "INVALID", "validation_errors": []}]}
    )
    assert report.sub_skills[0].state == "INVALID"
