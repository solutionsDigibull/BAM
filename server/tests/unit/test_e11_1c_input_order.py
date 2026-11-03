"""#624: the fresh-state key of generated code does not depend on JSONB key order."""

import copy
import json
from typing import Any

from fixtures import fixture_path
from sbam_contracts.manifest import SkillManifest
from sbam_workers.build.codegen.params import params_from_manifest


def manifest() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8")
    )
    return raw


def jsonb(value: Any) -> Any:
    """What PostgreSQL does to a JSON object: keys sorted by length, then bytes."""
    if isinstance(value, dict):
        return {k: jsonb(value[k]) for k in sorted(value, key=lambda k: (len(k.encode()), k))}
    if isinstance(value, list):
        return [jsonb(v) for v in value]
    return value


def inputs(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = params_from_manifest(SkillManifest.model_validate(data))["inputs"]
    return out


def key_of(data: dict[str, Any]) -> str:
    return next(f["name"] for f in inputs(data) if f["required"])


def test_e11_1c_the_key_and_the_signature_survive_a_jsonb_round_trip() -> None:
    data = manifest()
    assert key_of(data) == "mobile_number"
    assert key_of(jsonb(data)) == "mobile_number"
    assert [f["name"] for f in inputs(jsonb(data))] == [f["name"] for f in inputs(data)]


def test_e11_1c_the_required_list_decides_the_key_whatever_the_property_lengths() -> None:
    data = manifest()
    schema = data["contracts"]["input_schema"]
    schema["properties"] = {
        "a_very_long_property_name_that_sorts_last": {"type": "string"},
        "id": {"type": "string"},
        "short": {"type": "string"},
    }
    schema["required"] = ["a_very_long_property_name_that_sorts_last", "short"]
    assert key_of(data) == "a_very_long_property_name_that_sorts_last"
    assert key_of(jsonb(data)) == "a_very_long_property_name_that_sorts_last"


def test_e11_1c_optional_inputs_follow_the_required_ones_sorted_by_name() -> None:
    data = manifest()
    schema = data["contracts"]["input_schema"]
    schema["properties"]["zeta"] = {"type": "string"}
    schema["properties"]["alpha"] = {"type": "string"}
    names = [f["name"] for f in inputs(data)]
    assert names == [*schema["required"], "alpha", "zeta"]
    assert [f["required"] for f in inputs(data)] == [True, True, True, False, False]


def test_e11_1c_a_required_name_without_a_property_is_ignored_here() -> None:
    data = copy.deepcopy(manifest())
    data["contracts"]["input_schema"]["required"].append("ghost")
    assert "ghost" not in [f["name"] for f in inputs(data)]
