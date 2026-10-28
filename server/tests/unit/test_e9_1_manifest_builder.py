"""E9.1: `build_skill_manifest`, the Skill Manifest builder (no I/O), on the ISA14 MS01.1 sample."""

import copy
import json
import os
import subprocess
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fixtures import fixture_path
from sbam_contracts.canonical import canonical_json, deterministic_hash
from sbam_contracts.manifest import SCHEMA_DIR, TemplateRef
from sbam_contracts.state_machines import SubSkillState
from sbam_workers.build.manifest import (
    PLATFORM_SRIM,
    BuildContext,
    ManifestInvalid,
    build_skill_manifest,
)

STRUCTURED = fixture_path("isa14", "ms01_1_structured.json")
CTX = BuildContext(
    state=SubSkillState.VALID,
    validated_at=datetime(2026, 10, 28, 10, 0, tzinfo=UTC),
    ids={
        "tenant_id": "0190f3a2-7b1c-7d4e-8f00-000000000001",
        "project_id": "0190f3a2-7b1c-7d4e-8f00-000000000002",
        "workflow_id": "0190f3a2-7b1c-7d4e-8f00-000000000003",
        "sub_skill_id": "0190f3a2-7b1c-7d4e-8f00-000000000004",
    },
    provenance={
        "sow_version_id": "0190f3a2-7b1c-7d4e-8f00-000000000005",
        "sow_sha256": "ab" * 32,
        "approved_at": "2026-10-27T09:00:00Z",
        "approved_by": "fde-1",
        "knowledge_version_id": "0190f3a2-7b1c-7d4e-8f00-000000000006",
        "okf_sha256": "cd" * 32,
        "master_snapshot_sha256": "sha256:" + "ef" * 32,
        "sow_refs": ["F-ACCESS-1"],
    },
    template=TemplateRef(name="fastmcp-loopy", version="1.0.0", sha256="12" * 32),
    runtime_digest="sha256:" + "34" * 32,
)


def sample() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(STRUCTURED.read_text(encoding="utf-8"))
    return raw


def build(raw: dict[str, Any] | None = None, **ctx: Any) -> tuple[dict[str, Any], list[str]]:
    raw = raw if raw is not None else sample()
    context = CTX if not ctx else BuildContext(**{**CTX.__dict__, **ctx})
    manifest, rejected = build_skill_manifest(
        raw, macro_category=raw["skill_metadata"]["macro_category"], ctx=context
    )
    return manifest.dump(), rejected


def with_max_iterations(value: Any) -> dict[str, Any]:
    """The sample with another `max_iterations`, its I.bound saying so (rule 3)."""
    raw = sample()
    raw["isatvon_definition"]["v_variables"]["max_iterations"] = value
    raw["isatvon_definition"]["i_instructions"]["bound"] = f"Allow up to {value} OTP retry loops"
    return raw


def reorder(value: Any) -> Any:
    """The same JSON with every object's keys in reverse insertion order."""
    if isinstance(value, dict):
        return {k: reorder(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [reorder(v) for v in value]
    return value


def test_e9_1_t01_ms01_1_manifest_has_six_blocks_and_is_schema_valid() -> None:
    manifest, rejected = build()
    assert list(manifest) == [
        "skill_metadata",
        "isatvon_definition",
        "srim_runtime_definition",
        "contracts",
        "governance",
        "sbam_build_record",
    ]
    meta = manifest["skill_metadata"]
    assert (meta["skill_id"], meta["skill_type"], meta["version"], meta["status"]) == (
        "MS01.1_Investor_Auth_Manager",
        "Deterministic_Loopy_MCP",
        "1.0.0",
        "DRAFT",
    )
    assert meta["created_at"] == meta["updated_at"] == "2026-10-28T10:00:00Z"  # validated_at
    assert manifest["governance"]["versioning"] == {"major": 1, "minor": 0, "patch": 0}
    assert manifest["governance"]["lifecycle"]["state"] == "DRAFT"
    assert manifest["governance"]["deterministic_hash"] == deterministic_hash(manifest)
    srim = manifest["srim_runtime_definition"]
    assert srim["orchestration"] == PLATFORM_SRIM["orchestration"]
    assert srim["persistence"]["table"] == "investor_profiles, auth_event_audit_handoff"
    flat = manifest["isatvon_definition"]
    assert flat["i_instructions"].startswith("Triggered by login request.")
    assert manifest["sbam_build_record"]["isatvon_detail"] == sample()["isatvon_definition"]
    assert manifest["sbam_build_record"]["build"]["schema_sql_sha256"]
    schema = json.loads((SCHEMA_DIR / "skill_manifest.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(manifest, schema)
    assert rejected == []


def test_e9_1_status_follows_the_sub_skill_state() -> None:
    manifest, _ = build(state=SubSkillState.BUILDING)
    assert manifest["skill_metadata"]["status"] == "VALIDATING"
    assert manifest["governance"]["lifecycle"]["state"] == "VALIDATING"


def test_e9_1_t02_same_isatvon_same_bytes_in_any_key_order_and_process(tmp_path: Path) -> None:
    first, _ = build()
    second, _ = build(reorder(sample()))
    assert canonical_json(first) == canonical_json(second)
    assert first["governance"]["deterministic_hash"] == second["governance"]["deterministic_hash"]
    # a separate interpreter (another hash seed, another dict history) gets the same hash
    script = tmp_path / "build.py"
    tests = Path(__file__).parents[1]
    script.write_text(
        "import sys\n"
        f"sys.path[:0] = [{str(Path(__file__).parent)!r}, {str(tests)!r}]\n"
        "import test_e9_1_manifest_builder as t\n"
        "print(t.build(t.reorder(t.sample()))[0]['governance']['deterministic_hash'])\n",
        encoding="utf-8",
    )
    out = subprocess.run(  # noqa: S603 - fixed argv, test-owned script
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=True,
        env=dict(os.environ) | {"PYTHONHASHSEED": "12345"},
    )
    assert out.stdout.strip() == first["governance"]["deterministic_hash"]


def test_e9_1_t03_hash_ignores_evidence_and_certification_only() -> None:
    manifest, _ = build()
    record = manifest["sbam_build_record"]
    record["evidence"] = {
        "code_attempt_id": "a1",
        "bundle_sha256": "ab" * 32,
        "gates": [],
        "test_run_id": "t1",
        "sandbox_run_id": "s1",
        "passed_cases": 12,
    }
    record["certification"] = {"state": "CERTIFIED", "certified_at": "2026-10-29T10:00:00Z"}
    assert deterministic_hash(manifest) == manifest["governance"]["deterministic_hash"]
    changed, _ = build(with_max_iterations(4))
    assert (
        changed["governance"]["deterministic_hash"] != manifest["governance"]["deterministic_hash"]
    )


def test_e9_1_t04_contracts_from_s_and_o() -> None:
    manifest, _ = build()
    inputs, outputs = manifest["contracts"]["input_schema"], manifest["contracts"]["output_schema"]
    required = ["mobile_number", "otp_payload", "device_fingerprint"]
    assert inputs["required"] == required and list(inputs["properties"]) == required
    assert outputs["properties"]["auth_status"] == {
        "type": "string",
        "enum": ["AUTHENTICATED", "REJECTED", "LOCKED"],
    }
    assert outputs["properties"]["state_handoff_id"] == {"type": "string"}


def test_e9_1_t05_coupled_tools_are_an_isatvon_contract_failure() -> None:
    raw = sample()
    tools = raw["isatvon_definition"]["t_tech_stack"]
    tools["fixed_check_tool"] = tools["action_tool"] = "verify_otp"
    with pytest.raises(ManifestInvalid) as caught:
        build(raw)
    assert any(v.rule == 4 for v in caught.value.violations)


@pytest.mark.parametrize("value", [1, 10])
def test_e9_1_t06_max_iterations_bounds_are_accepted(value: int) -> None:
    build(with_max_iterations(value))


@pytest.mark.parametrize("value", [0, 11, "3"])
def test_e9_1_t06_max_iterations_out_of_range_or_a_string_is_rejected(value: Any) -> None:
    with pytest.raises(ManifestInvalid) as caught:
        build(with_max_iterations(value))
    assert any(v.path.endswith("v_variables.max_iterations") for v in caught.value.violations)


def test_e9_1_t07_platform_fields_in_the_input_are_dropped_and_reported() -> None:
    raw = sample()
    raw["skill_metadata"]["status"] = "APPROVED"  # e.g. a corrector patch
    raw["governance"] = {"lifecycle": {"state": "APPROVED"}}
    manifest, rejected = build(raw)
    assert manifest["skill_metadata"]["status"] == "DRAFT"
    assert manifest["governance"]["lifecycle"]["state"] == "DRAFT"
    assert rejected == ["governance", "skill_metadata.status"]
    assert manifest == build()[0]  # as if the patch had never been there


def test_e9_1_t09_rfc8785_vectors() -> None:
    """The canonicaliser is `rfc8785`; its vectors run in test_canonical.py (E0.3-T05)."""
    assert canonical_json({"b": [1.0, 1e30, 4.50, 2e-3], "a": "€"}) == (
        '{"a":"€","b":[1,1e+30,4.5,0.002]}'.encode()
    )


def test_e9_1_nfd_and_nfc_names_hash_the_same() -> None:
    raw = sample()
    raw["skill_metadata"]["skill_name"] = unicodedata.normalize("NFD", "Investor Vérifier")
    nfd, _ = build(raw)
    raw["skill_metadata"]["skill_name"] = unicodedata.normalize("NFC", "Investor Vérifier")
    nfc, _ = build(raw)
    assert nfd == nfc


def test_e9_1_float_and_int_literals_hash_the_same() -> None:
    raw = sample()
    raw["isatvon_definition"]["v_variables"]["parameters"][0]["value"] = 3.0
    as_float, _ = build(raw)
    as_int, _ = build()
    assert (
        as_float["governance"]["deterministic_hash"] == as_int["governance"]["deterministic_hash"]
    )


def test_e9_1_empty_fresh_state_is_valid() -> None:
    raw = copy.deepcopy(sample())
    raw["isatvon_definition"]["s_source"]["fresh_state_parameters"] = []
    variables = raw["isatvon_definition"]["v_variables"]  # gates that read no fresh state
    variables["no_op_condition"] = "otp_match == False"
    variables["escalation_condition"] = "policy_violation == True"
    manifest, _ = build(raw)
    assert manifest["isatvon_definition"]["s_source"]["fresh_state_parameters"] == []
