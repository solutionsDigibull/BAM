"""E14.3: when an earlier certificate may be carried into a new version."""

import copy
import json
from typing import Any

from fixtures import fixture_path
from sbam_workers.certify.reuse import (
    EVIDENCE_TAMPERED,
    NO_PRIOR_CERTIFICATE,
    ReuseParts,
    decide,
    parts_of,
)

SUITE = "5" * 64


def manifest() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8")
    )
    return raw


def parts(m: dict[str, Any] | None = None, suite: str = SUITE) -> ReuseParts:
    return parts_of(m or manifest(), suite)


def test_e14_3_identical_parts_and_verified_evidence_reuse() -> None:
    decision = decide(parts(), parts(), evidence_verified=True)
    assert decision.reuse and decision.differing == ()
    assert decision.prior_key == decision.current_key


def test_e14_3_t02_a_new_runtime_digest_means_no_reuse() -> None:
    changed = manifest()
    changed["sbam_build_record"]["build"]["runtime_image_digest"] = "sha256:" + "9" * 64
    decision = decide(parts(), parts(changed), evidence_verified=True)
    assert not decision.reuse and decision.differing == ("runtime_image_digest",)


def test_e14_3_t03_a_changed_suite_means_no_reuse() -> None:
    decision = decide(parts(), parts(suite="6" * 64), evidence_verified=True)
    assert not decision.reuse and decision.differing == ("suite_sha256",)


def test_e14_3_t04_a_new_template_version_or_hash_means_no_reuse() -> None:
    patched = manifest()
    patched["sbam_build_record"]["build"]["template"]["version"] = "9.9.9"
    assert decide(parts(), parts(patched), evidence_verified=True).differing == ("template",)
    same_version_other_hash = manifest()
    same_version_other_hash["sbam_build_record"]["build"]["template"]["sha256"] = "7" * 64
    assert not decide(parts(), parts(same_version_other_hash), evidence_verified=True).reuse


def test_e14_3_a_renamed_skill_is_a_change() -> None:
    renamed = manifest()
    renamed["skill_metadata"]["skill_name"] = "Something else"
    assert decide(parts(), parts(renamed), evidence_verified=True).differing == ("skill_name",)


def test_e14_3_a_changed_isatvon_or_contract_means_no_reuse() -> None:
    isatvon = manifest()
    isatvon["isatvon_definition"]["v_variables"]["max_iterations"] = 9
    assert decide(parts(), parts(isatvon), evidence_verified=True).differing == (
        "isatvon_definition",
    )
    contract = copy.deepcopy(manifest())
    contract["contracts"]["input_schema"] = {"type": "object", "title": "changed"}
    assert "contracts" in decide(parts(), parts(contract), evidence_verified=True).differing


def test_e14_3_t06_version_status_and_timestamps_are_not_part_of_the_key() -> None:
    """The new Workflow version bumps these; the Sub-Skill is still the same Sub-Skill."""
    later = manifest()
    later["skill_metadata"]["version"] = "1.0.1"
    later["skill_metadata"]["updated_at"] = "2030-01-01T00:00:00+00:00"
    later["skill_metadata"]["status"] = "SUPERSEDED"
    later["sbam_build_record"]["certification"] = None
    assert decide(parts(), parts(later), evidence_verified=True).reuse


def test_e14_3_evidence_that_no_longer_verifies_refuses_reuse() -> None:
    decision = decide(parts(), parts(), evidence_verified=False)
    assert not decision.reuse and decision.reason == EVIDENCE_TAMPERED


def test_e14_3_no_earlier_certificate_means_a_full_build() -> None:
    decision = decide(None, parts(), evidence_verified=True)
    assert not decision.reuse and decision.reason == NO_PRIOR_CERTIFICATE


def test_e14_3_several_changes_are_all_named() -> None:
    changed = manifest()
    changed["sbam_build_record"]["build"]["runtime_image_digest"] = "sha256:" + "9" * 64
    decision = decide(parts(), parts(changed, suite="6" * 64), evidence_verified=True)
    assert set(decision.differing) == {"runtime_image_digest", "suite_sha256"}
    assert decision.prior_key != decision.current_key


def test_696_a_fastmcp_loopy_1_0_0_build_is_not_reused_as_1_0_1() -> None:
    old = manifest()
    template = old["sbam_build_record"]["build"]["template"]
    template.update(
        version="1.0.0", sha256="d682984986e20c58d4cfdf3d7dd47bcb8469996882a868613aa69c9d6fdda3a6"
    )
    new = copy.deepcopy(old)
    new["sbam_build_record"]["build"]["template"].update(
        version="1.0.1", sha256="8be343759f19c941ab5043419e489e9d0dc854ac47fc65d8363b547ce85076af"
    )
    decision = decide(parts(old), parts(new), evidence_verified=True)
    assert not decision.reuse and decision.differing == ("template",)
