"""E5.1-T09: every public method of the Cognee adapter takes a `ProjectScope` first."""

import inspect
import typing

from sbam_contracts.knowledge import DATASET_PATTERN, ProjectScope
from sbam_workers.knowledge.cognee_adapter import DATASET_RE, CogneeAdapter

EXPECTED = {"ensure_dataset", "add", "cognify", "search", "graph_page", "delete_dataset"}


def public_methods() -> dict[str, typing.Any]:
    return {
        name: fn
        for name, fn in inspect.getmembers(CogneeAdapter, inspect.isfunction)
        if not name.startswith("_")
    }


def test_e5_1_t09_adapter_api_surface() -> None:
    assert set(public_methods()) == EXPECTED


def test_e5_1_t09_every_public_method_takes_project_scope_first() -> None:
    for name, fn in public_methods().items():
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == "self", name
        first = params[1]
        assert first.name == "scope", name
        assert typing.get_type_hints(fn)["scope"] is ProjectScope, name
        assert first.default is inspect.Parameter.empty, f"{name}: the scope must be required"
        assert inspect.iscoroutinefunction(fn), name


def test_e5_1_dataset_pattern_is_the_contract_one() -> None:
    assert DATASET_RE.pattern == DATASET_PATTERN == r"^sbam_[0-9a-f-]{36}_[0-9a-f-]{36}$"
