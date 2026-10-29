"""E9.1b: the tenant SRIM runtime defaults contract (O-19): strict, complete, no `table`."""

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.manifest import (
    ISA14_SRIM_DEFAULTS,
    SrimDefaultsView,
    SrimRuntimeDefaults,
    SrimRuntimeDefinition,
)


def blocks() -> dict[str, Any]:
    return copy.deepcopy(ISA14_SRIM_DEFAULTS.model_dump(mode="json"))


def test_e9_1b_t01_the_isa14_defaults_are_valid_and_have_no_table() -> None:
    assert SrimRuntimeDefaults.model_validate(blocks()) == ISA14_SRIM_DEFAULTS
    assert set(blocks()["persistence"]) == {"database", "action"}


@pytest.mark.parametrize(
    "block", ["orchestration", "execution", "gateway", "persistence", "audit", "ui"]
)
def test_e9_1b_t02_a_missing_block_is_rejected(block: str) -> None:
    data = blocks()
    del data[block]
    with pytest.raises(ValidationError):
        SrimRuntimeDefaults.model_validate(data)


@pytest.mark.parametrize("value", ["", "   ", 5, None])
def test_e9_1b_t03_an_empty_or_non_string_value_is_rejected(value: object) -> None:
    data = blocks()
    data["gateway"]["route_via"] = value
    with pytest.raises(ValidationError):
        SrimRuntimeDefaults.model_validate(data)


def test_e9_1b_t04_a_tenant_cannot_set_the_derived_table_or_an_unknown_key() -> None:
    data = blocks()
    data["persistence"]["table"] = "anything"
    with pytest.raises(ValidationError):
        SrimRuntimeDefaults.model_validate(data)
    data = blocks()
    data["extra_block"] = {"a": "b"}
    with pytest.raises(ValidationError):
        SrimRuntimeDefaults.model_validate(data)


def test_e9_1b_t05_the_runtime_definition_adds_the_sub_skills_table() -> None:
    definition = ISA14_SRIM_DEFAULTS.runtime_definition("a_table, b_table")
    assert isinstance(definition, SrimRuntimeDefinition)
    assert definition.persistence.table == "a_table, b_table"
    assert definition.persistence.database == ISA14_SRIM_DEFAULTS.persistence.database
    assert definition.gateway == ISA14_SRIM_DEFAULTS.gateway


def test_e9_1b_t06_the_view_says_whether_the_values_are_the_defaults() -> None:
    view = SrimDefaultsView(defaults=ISA14_SRIM_DEFAULTS, is_default=True)
    assert view.dump()["is_default"] is True
