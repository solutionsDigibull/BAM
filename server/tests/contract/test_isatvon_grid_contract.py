"""E8.6 contract: the grid metadata and the SubSkill model cannot diverge (fails CI if they do).

The model is walked here independently of the code under test (Pydantic field info, not the JSON
Schema), so a bug in the schema walk cannot hide a missing column.
"""

import types
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel
from sbam_api.isatvon import grid
from sbam_contracts.isatvon14 import SubSkill, schemas


def _model_of(annotation: Any) -> type[BaseModel] | None:
    """The nested model behind `Model` or `Model | None`; None for scalars, lists and dicts."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (Union, types.UnionType):
        models = [m for a in get_args(annotation) if (m := _model_of(a))]
        return models[0] if models else None
    return None


def model_leaves(model: type[BaseModel], prefix: str = "") -> dict[str, bool]:
    """Dotted leaf path -> required, for every field that is not a nested model."""
    found: dict[str, bool] = {}
    for name, info in model.model_fields.items():
        nested = _model_of(info.annotation)
        if nested is not None:
            found.update(model_leaves(nested, f"{prefix}{name}."))
        else:
            found[f"{prefix}{name}"] = info.is_required()
    return found


def columns() -> dict[str, grid.GridColumn]:
    payload = grid.render("1.4")
    assert payload is not None
    flat = [c for g in payload.payload.grid.column_groups for c in g.children]
    assert len({c.field_path for c in flat}) == len(flat), "a field has more than one column"
    return {c.field_path: c for c in flat}


def test_e8_6_t01_every_model_leaf_has_exactly_one_column() -> None:
    leaves = model_leaves(SubSkill)
    cols = columns()
    model_cols = {p for p, c in cols.items() if p in leaves}
    assert model_cols == set(leaves)
    # The model-derived columns are exactly the model's leaves; the rest are platform row fields.
    assert {p for p in cols if p not in leaves} == {f[0] for f in grid.PLATFORM_FIELDS}
    for path, required in leaves.items():
        assert cols[path].required is required, path
    for bound in ("max_iterations", "success_gate", "no_op_condition", "escalation_condition"):
        assert f"isatvon_definition.v_variables.{bound}" in cols
    assert "isatvon_definition.v_variables.budget_limit" in cols


def test_e8_6_t01_every_model_column_path_resolves_in_the_json_schema() -> None:
    json_schema = schemas()["sub_skill"]
    platform_rows = {f[0] for f in grid.PLATFORM_FIELDS}
    for path in columns():
        assert grid.resolves(json_schema, path) is (path not in platform_rows), path
    assert not grid.resolves(json_schema, "isatvon_definition.v_variables.nope")
    assert not grid.resolves(json_schema, "state")


def test_e8_6_hints_point_at_real_model_fields() -> None:
    leaves = model_leaves(SubSkill)
    hinted = set(grid.EDITOR_OVERRIDES) | set(grid.RANGE_OVERRIDES) | set(grid.READONLY_PATHS)
    assert hinted <= set(leaves), hinted - set(leaves)


def test_e8_6_every_model_leaf_belongs_to_a_group() -> None:
    for path in model_leaves(SubSkill):
        assert grid._group_of(path) in grid.MODEL_GROUPS
