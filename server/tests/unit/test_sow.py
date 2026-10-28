"""E0.5 unit tests: SowDocument model, breakdown hints and committed JSON Schema (TD §10.6)."""

import copy
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from pydantic import ValidationError
from sbam_contracts.sow import SECTIONS, SowDocument, breakdown_hints, schemas

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas" / "sow"


def sow(name: str = "ms01_1_sow.json") -> dict[str, Any]:
    data = json.loads(fixture_path("sow", name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def errors(data: dict[str, Any]) -> list[tuple[str, str]]:
    """(dotted path, message) of every validation error."""
    with pytest.raises(ValidationError) as info:
        SowDocument.model_validate(data)
    drop = {"okf", "fde"}  # the citation union's tag is not part of the document path
    return [
        (".".join(str(p) for p in e["loc"] if p not in drop), e["msg"]) for e in info.value.errors()
    ]


# --- T01 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ms01_1_sow.json", "sow_rfq_nav9.json"])
def test_e0_5_t01_fixture_validates(name: str) -> None:
    doc = SowDocument.model_validate(sow(name))
    assert tuple(type(doc).model_fields) == SECTIONS


def test_e0_5_t01_json_schema_equals_committed_snapshot() -> None:
    committed = json.loads((SCHEMAS / "sow_document.schema.json").read_text(encoding="utf-8"))
    assert (
        committed == schemas()["sow_document"]
    ), "regenerate: cd server && uv run python -m sbam_contracts.sow"
    assert committed["additionalProperties"] is False


# --- T02-T06 --------------------------------------------------------------------------------------


def test_e0_5_t02_missing_section() -> None:
    data = sow()
    del data["features"]
    assert errors(data) == [("features", "Field required")]


def test_e0_5_sections_must_be_in_td_order() -> None:
    data = sow()
    reordered = {"scope": data.pop("scope"), **data}
    assert "TD §10.6 order" in errors(reordered)[0][1]


def test_e0_5_t03_citations_are_mandatory() -> None:
    data = sow()
    data["features"][0]["citations"] = []
    path, message = errors(data)[0]
    assert path == "features.0.citations"
    assert "at least 1 item" in message


def test_e0_5_t03_citation_needs_uuid_and_location() -> None:
    data = sow()
    data["overview"]["citations"][0]["document_version_id"] = "fixture:documents/text.pdf"
    data["scope"]["citations"][0]["location"] = {}
    paths = [p for p, _ in errors(data)]
    assert "overview.citations.0.document_version_id" in paths
    assert "scope.citations.0.location" in paths


def test_e6_3b_t00_an_fde_citation_is_a_valid_citation_without_a_source() -> None:
    data = sow()
    fde = {"kind": "fde", "author": "Asha", "date": "2026-10-28", "note": "Call with the client"}
    data["overview"]["citations"] = [fde]
    doc = SowDocument.model_validate(data)
    assert doc.overview.citations[0].model_dump(mode="json") == fde


def test_e6_3b_t00_an_fde_citation_cannot_carry_an_okf_ref_id() -> None:
    data = sow()
    fde = {"kind": "fde", "author": "Asha", "date": "2026-10-28", "okf_ref_id": "fake"}
    data["overview"]["citations"] = [fde]
    assert errors(data)


def test_e0_5_t04_duplicate_feature_ids() -> None:
    data = sow()
    data["features"][0]["id"] = data["features"][1]["id"] = "F-01.1"
    assert "duplicate feature id F-01.1" in errors(data)[0][1]


def test_e0_5_t05_tool_decoupling_at_sow_level() -> None:
    data = sow()
    data["features"][0]["tools"] = {"action": ["x"], "check": ["x"]}
    path, message = errors(data)[0]
    assert path == "features.0.tools"
    assert "check tool must differ from action tool" in message


def test_e0_5_t06_module_requires_a_feature() -> None:
    data = sow()
    reporting = copy.deepcopy(data["modules"][0])
    reporting.update(id="M-REPORTING", name="Reporting")
    data["modules"].append(reporting)
    assert "module M-REPORTING requires at least 1 feature" in errors(data)[0][1]


def test_e0_5_feature_must_name_a_known_module() -> None:
    data = sow()
    data["features"][1]["module_id"] = "M-NOWHERE"
    assert "unknown module M-NOWHERE" in errors(data)[0][1]


# --- T07: text is data ----------------------------------------------------------------------------


def test_e0_5_t07_control_chars_stripped_markup_kept_as_text() -> None:
    data = sow()
    data["overview"]["background"] = "<script>alert(1)</script>\x07"
    doc = SowDocument.model_validate(data)
    assert doc.overview.background == "<script>alert(1)</script>"


# --- T08: performance -----------------------------------------------------------------------------


def large_sow(modules: int, per_module: int) -> dict[str, Any]:
    data = sow()
    module, feature = data["modules"][0], data["features"][0]
    data["modules"] = [{**module, "id": f"M-{m}"} for m in range(modules)]
    data["features"] = [
        {**feature, "id": f"F-{m}.{f}", "module_id": f"M-{m}"}
        for m in range(modules)
        for f in range(per_module)
    ]
    return data


def test_e0_5_t08_large_sow_validates_quickly() -> None:
    data = large_sow(50, 10)
    start = time.perf_counter()
    doc = SowDocument.model_validate(data)
    assert time.perf_counter() - start < 0.5
    assert len(doc.features) == 500


# --- Edge cases and safety ------------------------------------------------------------------------


def test_e0_5_bound_policy_is_required_not_free_text() -> None:
    data = sow()
    data["features"][0]["bound"] = "3 retries"
    assert errors(data)[0][0] == "features.0.bound"
    data["features"][0]["bound"] = {"max_attempts": 11, "on_exhaustion": "stop"}
    assert errors(data)[0][0] == "features.0.bound.max_attempts"


@pytest.mark.parametrize("field", ["status", "approved_by", "sha256", "version"])
def test_e0_5_platform_fields_cannot_be_set(field: str) -> None:
    data = sow()
    data[field] = "x"
    assert any(path == field for path, _ in errors(data))


def test_e0_5_rule_refs_must_be_rules_master_ids() -> None:
    data = sow()
    data["rules_constraints"]["rule_refs"] = ["auth lockout"]
    assert "RULE-*" in errors(data)[0][1]


def test_e0_5_no_type_coercion() -> None:
    data = sow()
    data["features"][0]["bound"]["max_attempts"] = "3"
    assert errors(data)[0][0] == "features.0.bound.max_attempts"


# --- Breakdown hints ------------------------------------------------------------------------------


def test_e0_5_breakdown_hints_map_features_to_isatvon_fields() -> None:
    doc = SowDocument.model_validate(sow())
    (agent,) = breakdown_hints(doc)
    assert agent["module_id"] == "M-ACCESS"
    assert [s["feature_id"] for s in agent["sub_skills"]] == ["F-ACCESS-1", "F-ACCESS-2"]
    first = agent["sub_skills"][0]
    feature = doc.features[0]
    assert (first["i_trigger"], first["i_action"], first["i_check"]) == (
        feature.trigger,
        feature.action,
        feature.check,
    )
    assert first["v_max_iterations"] == 3
    assert "3" in first["i_bound"]
    assert first["t_action_tools"] == ["Auth_MCP"]
    assert first["t_check_tools"] == ["RBAC_MCP"]
    assert first["o_handoff"] == feature.handoff


def test_e0_5_breakdown_hints_group_by_module_in_order() -> None:
    doc = SowDocument.model_validate(sow("sow_rfq_nav9.json"))
    hints = breakdown_hints(doc)
    assert [h["module_id"] for h in hints] == ["M-INTAKE", "M-COST"]
    assert [len(h["sub_skills"]) for h in hints] == [2, 1]
