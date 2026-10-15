"""E8.6 unit tests: editor choice and read-only platform fields of the grid metadata."""

from typing import Any

import pytest
from sbam_api.isatvon import grid
from sbam_contracts.state_machines import SubSkillState

IDEF = "isatvon_definition."
ALLOWED = {"text", "largeText", "number", "boolean", "json", "expression", "stringList", "select"}


def columns() -> dict[str, grid.GridColumn]:
    rendered = grid.render("1.4")
    assert rendered is not None
    return {c.field_path: c for g in rendered.payload.grid.column_groups for c in g.children}


def test_e8_6_t05_editors_follow_the_field() -> None:
    cols = columns()
    bound = cols[IDEF + "v_variables.max_iterations"]
    assert (bound.editor, bound.min, bound.max, bound.type) == ("number", 1, 10, "integer")
    for gate in ("success_gate", "no_op_condition", "escalation_condition"):
        assert cols[IDEF + f"v_variables.{gate}"].editor == "expression"
    assert cols[IDEF + "o_outcome.json_schema"].editor == "json"
    assert cols[IDEF + "v_variables.secrets"].editor == "stringList"
    assert cols[IDEF + "t_tech_stack.libraries"].editor == "json"  # list of models


def test_e8_6_t05_editors_stay_inside_the_allow_list() -> None:
    assert {e.value for e in grid.Editor} == ALLOWED | {"readonly"}
    assert {c.editor.value for c in columns().values()} <= ALLOWED | {"readonly"}


@pytest.mark.parametrize(
    ("node", "editor"),
    [
        ({"type": "boolean"}, "boolean"),
        ({"type": "integer"}, "number"),
        ({"type": "number"}, "number"),
        ({"type": "string", "maxLength": 200}, "text"),
        ({"type": "string", "maxLength": 4000}, "largeText"),
        ({"type": "string"}, "text"),
        ({"type": "string", "enum": ["A", "B"]}, "select"),
        ({"type": "array", "items": {"type": "string"}}, "stringList"),
        ({"type": "array", "items": {"type": "object"}}, "json"),
        ({"type": "object"}, "json"),
    ],
)
def test_e8_6_unhinted_field_gets_the_editor_for_its_type(
    node: dict[str, Any], editor: str
) -> None:
    assert grid.default_editor(node) == editor


def test_e8_6_t06_platform_fields_are_read_only() -> None:
    cols = columns()
    for path in ("state", "skill_id", "content_sha256", "validation_errors"):
        col = cols["skill_metadata.skill_id" if path == "skill_id" else path]
        assert col.editable is False, path
        assert col.editor == "readonly", path
        assert col.platform is True, path
    assert cols["state"].enum == [s.value for s in SubSkillState]
    editable = [p for p, c in cols.items() if not c.platform]
    assert editable and all(cols[p].editable for p in editable)


def test_e8_6_unknown_version_is_not_rendered() -> None:
    assert grid.render("9.9") is None
    assert grid.render("1.4.0") is None


def test_e8_6_etag_is_stable_and_strong() -> None:
    rendered = grid.render("1.4")
    assert rendered is not None
    assert rendered.etag.startswith('"') and rendered.etag.endswith('"')
    assert not rendered.etag.startswith("W/")
