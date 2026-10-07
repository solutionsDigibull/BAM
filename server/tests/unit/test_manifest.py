"""E0.3 unit tests: Skill Manifest, Workflow manifest and loop_spec models (TD §11.1-11.5)."""

import copy
import json
import random
import re
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sbam_contracts.canonical import canonical_json, deterministic_hash
from sbam_contracts.gate_expr import ExpressionError
from sbam_contracts.isatvon14 import SubSkill, VVariables
from sbam_contracts.manifest import (
    UI_UPDATE_TEMPLATE,
    Isa14Manifest,
    LoopSpec,
    SkillManifest,
    WorkflowManifest,
    schemas,
)

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas" / "manifest"
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def load(name: str) -> dict[str, Any]:
    data = json.loads(fixture_path("isa14", name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def isa14_sample() -> dict[str, Any]:
    return load("ms01_1_sample.json")


def manifest() -> dict[str, Any]:
    return load("ms01_1_manifest.json")


def rejected(data: dict[str, Any], model: type[Isa14Manifest] = SkillManifest) -> str:
    with pytest.raises(ValidationError) as info:
        model.model_validate(data)
    return str(info.value)


# --- T01: round trip ------------------------------------------------------------------------------


def test_e0_3_t01_isa14_sample_round_trips_byte_identically() -> None:
    x = isa14_sample()
    assert canonical_json(Isa14Manifest.model_validate(x).dump()) == canonical_json(x)


def test_e0_3_t01_sbam_manifest_round_trips_and_carries_its_own_hash() -> None:
    x = manifest()
    model = SkillManifest.model_validate(x)
    assert canonical_json(model.dump()) == canonical_json(x)
    assert model.governance.deterministic_hash == model.computed_hash() == deterministic_hash(x)


def test_e0_3_sealed_fills_the_hash() -> None:
    x = manifest()
    x["governance"]["deterministic_hash"] = "sha256:" + "0" * 64
    sealed = SkillManifest.sealed(x)
    assert sealed.governance.deterministic_hash == manifest()["governance"]["deterministic_hash"]


# --- T02 / T03: what the hash covers --------------------------------------------------------------


def test_e0_3_t02_excluded_fields_do_not_change_the_hash() -> None:
    x = manifest()
    x["sbam_build_record"]["evidence"]["test_run_id"] = "tr-9999"
    x["sbam_build_record"]["certification"]["certified_at"] = "2027-01-01T00:00:00Z"
    x["governance"]["deterministic_hash"] = "sha256:" + "f" * 64
    assert deterministic_hash(x) == deterministic_hash(manifest())


def test_e0_3_t03_included_field_changes_the_hash() -> None:
    x = manifest()
    x["isatvon_definition"]["v_variables"]["max_iterations"] = 4
    changed = deterministic_hash(x)
    assert changed != deterministic_hash(manifest())
    assert HASH.match(changed)


def test_e0_3_certification_reuse_keeps_the_hash() -> None:
    x = manifest()
    x["sbam_build_record"]["certification"]["reused_from"] = "cert-0001"
    SkillManifest.model_validate(x)
    assert deterministic_hash(x) == deterministic_hash(manifest())


# --- T04: determinism property --------------------------------------------------------------------


def _shuffled(value: Any, rng: random.Random) -> Any:
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {k: _shuffled(v, rng) for k, v in items}
    if isinstance(value, list):
        return [_shuffled(v, rng) for v in value]
    return value


@settings(max_examples=500, deadline=None)
@given(
    st.randoms(use_true_random=False),
    st.sampled_from([None, 0, 1, 2, 4, "\t"]),
    st.sampled_from([(",", ":"), (", ", ": "), (" ,", " : ")]),
    st.booleans(),
)
def test_e0_3_t04_key_order_and_whitespace_never_change_the_hash(
    rng: random.Random, indent: int | str | None, separators: tuple[str, str], ascii_only: bool
) -> None:
    original = manifest()
    text = json.dumps(
        _shuffled(original, rng), indent=indent, separators=separators, ensure_ascii=ascii_only
    )
    assert deterministic_hash(json.loads(text)) == deterministic_hash(original)


# --- T06: version format --------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["1.1.0", "1.0", "v1.0.0"])
def test_e0_3_t06_bad_versions_are_rejected(version: str) -> None:
    x = manifest()
    x["skill_metadata"]["version"] = version
    assert "is not MAJOR.0.PATCH" in rejected(x)


def test_e0_3_t06_version_must_agree_with_versioning() -> None:
    x = manifest()
    x["skill_metadata"]["version"] = "2.0.3"
    x["governance"]["versioning"] = {"major": 2, "minor": 0, "patch": 3}
    SkillManifest.sealed(x)
    x["governance"]["versioning"] = {"major": 2, "minor": 0, "patch": 4}
    assert "does not match governance.versioning" in rejected(x)
    x["governance"]["versioning"] = {"major": 2, "minor": 1, "patch": 3}
    assert "minor must be 0" in rejected(x)


# --- T07: shape and enums -------------------------------------------------------------------------


def test_e0_3_t07_extra_top_level_block_is_rejected() -> None:
    x = manifest()
    x["extra"] = {}
    assert "extra" in rejected(x)
    y = isa14_sample()
    y["extra"] = {}
    assert "extra" in rejected(y, Isa14Manifest)


def test_e0_3_t07_only_deterministic_loopy_mcp_skill_type() -> None:
    x = manifest()
    x["skill_metadata"]["skill_type"] = "Agentic_Loop"
    assert "O-15" in rejected(x)


@pytest.mark.parametrize("status", ["CERTIFIED", "approved", "DONE"])
def test_e0_3_unknown_status_is_rejected(status: str) -> None:
    x = manifest()
    x["skill_metadata"]["status"] = status
    x["governance"]["lifecycle"]["state"] = status
    assert "is not one of" in rejected(x)


@pytest.mark.parametrize(
    "stamp", ["2026-06-29 12:00", "2026-06-29T12:00:00+05:30", "2026-06-29T12:00:00"]
)
def test_e0_3_timestamps_must_be_iso_utc(stamp: str) -> None:
    x = manifest()
    x["skill_metadata"]["updated_at"] = stamp
    assert "skill_metadata.updated_at" in rejected(x)


def test_e0_3_c52_approved_manifest_must_be_immutable() -> None:
    x = manifest()
    x["governance"]["lifecycle"]["immutable_after_approval"] = False
    assert "C-52" in rejected(x)
    x["governance"]["lifecycle"]["state"] = "DRAFT"
    assert "must equal governance.lifecycle.state" in rejected(x)


def test_e0_3_hash_placeholder_only_before_approval() -> None:
    x = manifest()
    x["governance"]["deterministic_hash"] = "sha256:TO_BE_COMPUTED_ON_SAVE"
    assert "deterministic_hash" in rejected(x)
    x["skill_metadata"]["status"] = x["governance"]["lifecycle"]["state"] = "DRAFT"
    SkillManifest.model_validate(x)


def test_e0_3_next_is_required_in_the_sbam_manifest() -> None:
    x = manifest()
    del x["isatvon_definition"]["n_notification"]["next"]
    assert "next is required" in rejected(x)
    x["isatvon_definition"]["n_notification"]["next"] = None
    SkillManifest.model_validate(x)


def test_e0_3_contracts_required_empty_or_missing_are_both_kept() -> None:
    x = manifest()
    assert x["contracts"]["output_schema"]["required"] == []
    del x["contracts"]["output_schema"]["required"]
    model = SkillManifest.model_validate(x)
    assert "required" not in model.dump()["contracts"]["output_schema"]
    assert deterministic_hash(x) != deterministic_hash(manifest())  # different JSON, different hash


# --- Platform-only sbam_build_record --------------------------------------------------------------


def test_e0_3_sbam_build_record_is_rejected_in_fde_and_llm_inputs() -> None:
    record = manifest()["sbam_build_record"]
    sample = isa14_sample()
    sample["sbam_build_record"] = record
    assert "sbam_build_record" in rejected(sample, Isa14Manifest)
    sub_skill = load("ms01_1_structured.json")
    sub_skill["sbam_build_record"] = record
    with pytest.raises(ValidationError):
        SubSkill.model_validate(sub_skill)


def test_e0_3_build_record_reuses_the_structured_isatvon_definition() -> None:
    x = manifest()
    x["sbam_build_record"]["isatvon_detail"]["v_variables"]["max_iterations"] = "3"
    assert "isatvon_detail.v_variables.max_iterations" in rejected(x)


# --- T09: Workflow manifest -----------------------------------------------------------------------


def workflow() -> dict[str, Any]:
    skill = manifest()
    digest = "a" * 64
    entry = {
        "skill_id": skill["skill_metadata"]["skill_id"],
        "deterministic_hash": skill["governance"]["deterministic_hash"],
        "manifest_sha256": digest,
        "bundle_sha256": "b" * 64,
        "certification_id": "cert-ms01-1",
    }
    second = {
        **entry,
        "skill_id": "MS01.2_Investor_KYC_Verifier",
        "certification_id": "cert-ms01-2",
    }
    return {
        "schema_version": "sbam-workflow-manifest/1.0",
        "ids": {
            "tenant_id": "tnt-alpha",
            "project_id": "prj-investor-portal",
            "workflow_id": "wf-001",
        },
        "name": "Investor Portal",
        "objective": "Secure investor onboarding and access",
        "version": "1.0.0",
        "previous_version": None,
        "provenance": skill["sbam_build_record"]["provenance"],
        "isatvon_framework_version": "1.4",
        "macro_skills": [
            {
                "macro_category": "MS01_Investor_Registration_Access",
                "name": "Investor Registration & Access",
                "agent_ref": "AG-AUTH",
                "certification_id": "cert-ms01",
                "skills": [entry, second],
            }
        ],
        "schema_sql": {"artifact_key": "wf-001/schema.sql", "sha256": "c" * 64},
        "dag": {"nodes": [{"id": "MS01"}], "edges": []},
        "runtime_image_digest": "sha256:" + "d" * 64,
        "evidence_index": [
            {
                "level": "skill",
                "subject": "MS01.1",
                "certification_id": "cert-ms01-1",
                "sha256": digest,
            }
        ],
        "certification": {
            "state": "FROZEN",
            "certified_at": "2026-06-29T12:00:00Z",
            "frozen_at": None,
        },
        "integrity": {"content_sha256": "sha256:" + "0" * 64},
    }


def test_e0_3_t09_workflow_hash_covers_every_skill_hash() -> None:
    sealed = WorkflowManifest.sealed(workflow())
    assert sealed.integrity.content_sha256 == sealed.computed_hash()
    changed = workflow()
    changed["macro_skills"][0]["skills"][1]["deterministic_hash"] = "sha256:" + "e" * 64
    assert (
        WorkflowManifest.sealed(changed).integrity.content_sha256 != sealed.integrity.content_sha256
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("macro_skills", 0, "skills", 0, "bundle_sha256"), "9" * 64),
        (("schema_sql", "sha256"), "9" * 64),
    ],
)
def test_e0_3_workflow_hash_covers_bundle_and_schema_sql(path: tuple[Any, ...], value: str) -> None:
    changed = workflow()
    node: Any = changed
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert (
        WorkflowManifest.sealed(changed).computed_hash()
        != WorkflowManifest.sealed(workflow()).computed_hash()
    )


def test_e0_3_workflow_freeze_does_not_change_the_hash() -> None:
    frozen = workflow()
    frozen["certification"]["frozen_at"] = "2026-06-30T08:00:00Z"
    assert (
        WorkflowManifest.sealed(frozen).computed_hash()
        == WorkflowManifest.sealed(workflow()).computed_hash()
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema_version", "sbam-workflow-manifest/2.0"),
        ("isatvon_framework_version", "1.5"),
        ("version", "1.2.0"),
        ("macro_skills", []),
    ],
)
def test_e0_3_workflow_manifest_rules(key: str, value: Any) -> None:
    bad = workflow()
    bad[key] = value
    with pytest.raises(ValidationError):
        WorkflowManifest.sealed(bad)


def test_e0_3_workflow_skill_needs_manifest_and_bundle_hashes() -> None:
    bad = workflow()
    del bad["macro_skills"][0]["skills"][0]["bundle_sha256"]
    with pytest.raises(ValidationError):
        WorkflowManifest.sealed(bad)


# --- loop_spec ------------------------------------------------------------------------------------


def structured_v() -> tuple[VVariables, list[str]]:
    skill = SubSkill.model_validate(load("ms01_1_structured.json"))
    d = skill.isatvon_definition
    return d.v_variables, d.n_notification.ui_updates


def test_e0_3_loop_spec_equals_the_source_bounds() -> None:
    v, ui = structured_v()
    spec = LoopSpec.from_variables(v, ui)
    assert (spec.max_iterations, spec.budget_limit_ms) == (3, 5000)
    assert (spec.success_gate, spec.no_op_condition, spec.escalation_condition) == (
        v.success_gate,
        v.no_op_condition,
        v.escalation_condition,
    )
    assert spec.ui_update_template == (
        "Status: Verifying -> Looping [Pass {iteration}/{max_iterations}]: "
        "Incorrect OTP -> Authenticated"
    )
    assert spec.ui_update_template.format(iteration=2, max_iterations=3).count("[Pass 2/3]") == 1


def test_e0_3_loop_spec_defaults_template_and_rejects_bad_gates() -> None:
    v, _ = structured_v()
    assert LoopSpec.from_variables(v, ["Done"]).ui_update_template == UI_UPDATE_TEMPLATE
    bad = v.model_copy(update={"success_gate": "f(x)"})
    with pytest.raises(ExpressionError):
        LoopSpec.from_variables(bad, [])


# --- Committed JSON Schemas -----------------------------------------------------------------------


@pytest.mark.parametrize("stem", sorted(schemas()))
def test_e0_3_committed_schema_matches_model(stem: str) -> None:
    committed = json.loads((SCHEMAS / f"{stem}.schema.json").read_text(encoding="utf-8"))
    assert (
        committed == schemas()[stem]
    ), "regenerate: cd server && uv run python -m sbam_contracts.manifest"


def test_e0_3_fixture_is_not_mutated_by_tests() -> None:
    assert copy.deepcopy(manifest()) == manifest()
