"""E0.17: every ISA14 and SOW fixture is validated by its real model, and bad input is refused.

Before this file the valid fixtures were each checked in a different test module and nothing stopped
a new fixture from landing unchecked. Now `VALID` names the model of every JSON in `isa14/` and
`sow/`, a guard fails when a fixture is missing from it, and a table of deliberately broken copies
shows each kind of defect fails with the documented error. The broken copies are built in memory
from the valid fixtures, so they cannot drift from them.
"""

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
from fixtures import FIXTURES_DIR, fixture_path
from pydantic import ValidationError
from sbam_contracts.isatvon14 import validate_sub_skill
from sbam_contracts.manifest import Isa14Manifest, SkillManifest
from sbam_contracts.sow import SowDocument

Raw = dict[str, Any]


def load(relative: str) -> Raw:
    data: Raw = json.loads(fixture_path(*relative.split("/")).read_text(encoding="utf-8"))
    return data


# --- the models, one check per fixture kind ------------------------------------------------------


def check_isa14_manifest(raw: Raw) -> None:
    Isa14Manifest.model_validate(raw)


def check_skill_manifest(raw: Raw) -> None:
    manifest = SkillManifest.model_validate(raw)
    assert manifest.computed_hash() == raw["governance"]["deterministic_hash"], "hash mismatch"


def check_sow(raw: Raw) -> None:
    SowDocument.model_validate(raw)


class RuleViolationsError(Exception):
    """validate_sub_skill reports TD §10.4 violations as a list; this makes them an exception."""

    def __init__(self, found: list[tuple[int, str]]) -> None:
        super().__init__(f"TD §10.4 violations: {found}")
        self.found = found


def check_sub_skill(raw: Raw) -> None:
    found = [(v.rule, v.path) for v in validate_sub_skill(raw)]
    if found:
        raise RuleViolationsError(found)


VALID: dict[str, Callable[[Raw], None]] = {
    "isa14/ms01_1_sample.json": check_isa14_manifest,
    "isa14/ms01_1_structured.json": check_sub_skill,
    "isa14/ms01_1_manifest.json": check_skill_manifest,
    "sow/ms01_1_sow.json": check_sow,
    "sow/sow_rfq_nav9.json": check_sow,
}


def all_json_fixtures() -> set[str]:
    return {
        path.relative_to(FIXTURES_DIR).as_posix()
        for folder in ("isa14", "sow")
        for path in (FIXTURES_DIR / folder).rglob("*.json")
    }


def test_e0_17_every_isa14_and_sow_fixture_has_a_model() -> None:
    """A fixture added without a model (or a model entry without a fixture) fails here."""
    assert set(VALID) == all_json_fixtures()


@pytest.mark.parametrize("relative", sorted(VALID))
def test_e0_17_t01_real_models_accept_every_valid_fixture(relative: str) -> None:
    VALID[relative](load(relative))


# --- deliberately broken copies ------------------------------------------------------------------


def at(raw: Any, path: str) -> Any:
    """Follow a dotted path; a number steps into a list."""
    for part in path.split("."):
        raw = raw[int(part)] if isinstance(raw, list) else raw[part]
    return raw


def put(raw: Raw, path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    parent: Any = at(raw, ".".join(parents)) if parents else raw
    parent[int(leaf) if isinstance(parent, list) else leaf] = value


def drop(raw: Raw, path: str) -> None:
    *parents, leaf = path.split(".")
    parent: Any = at(raw, ".".join(parents)) if parents else raw
    del parent[int(leaf) if isinstance(parent, list) else leaf]


def sow_sections_swapped(raw: Raw) -> None:
    names = list(raw)
    raw.update({names[0]: raw.pop(names[0])})  # the first section moves to the end


def sow_module_without_feature(raw: Raw) -> None:
    extra = copy.deepcopy(raw["modules"][0])
    extra["id"] = "M-EMPTY"
    raw["modules"].append(extra)


def sow_check_tool_equals_action_tool(raw: Raw) -> None:
    tools = raw["features"][0]["tools"]
    tools["check"] = list(tools["action"])


SOWS = ["sow/ms01_1_sow.json", "sow/sow_rfq_nav9.json"]
Mutation = Callable[[Raw], None]

# (defect, fixtures it is applied to, how the copy is broken, text the error must contain)
SOW_DEFECTS: list[tuple[str, list[str], Mutation, str]] = [
    ("sections_out_of_order", SOWS, sow_sections_swapped, "sections must be in TD §10.6 order"),
    ("missing_section", SOWS, lambda r: drop(r, "scope"), "scope"),
    ("empty_modules", SOWS, lambda r: put(r, "modules", []), "at least 1 item"),
    ("empty_features", SOWS, lambda r: put(r, "features", []), "at least 1 item"),
    (
        "duplicate_feature_id",
        SOWS,
        lambda r: put(r, "features.1.id", r["features"][0]["id"]),
        "duplicate feature id",
    ),
    (
        "duplicate_module_id",
        ["sow/sow_rfq_nav9.json"],  # the only SOW fixture with two modules
        lambda r: put(r, "modules.1.id", r["modules"][0]["id"]),
        "duplicate module id",
    ),
    (
        "feature_names_unknown_module",
        SOWS,
        lambda r: put(r, "features.0.module_id", "M-NOPE"),
        "names unknown module M-NOPE",
    ),
    ("module_without_feature", SOWS, sow_module_without_feature, "requires at least 1 feature"),
    (
        "check_tool_same_as_action_tool",
        SOWS,
        sow_check_tool_equals_action_tool,
        "check tool must differ from action tool",
    ),
    ("uncited_feature", SOWS, lambda r: put(r, "features.0.citations", []), "at least 1 item"),
    (
        "citation_without_location",
        SOWS,
        lambda r: put(r, "features.0.citations.0.location", {}),
        "location needs at least one of page, line, sheet, cell",
    ),
    (
        "rule_ref_is_not_a_rules_master_id",
        SOWS,
        lambda r: put(r, "rules_constraints.rule_refs", ["NOT-A-RULE"]),
        "is not a Rules master id RULE-*",
    ),
    (
        "unknown_section",
        SOWS,
        lambda r: put(r, "bonus", {"x": 1}),
        "Extra inputs are not permitted",
    ),
    (
        "unknown_feature_field",
        SOWS,
        lambda r: put(r, "features.0.secret", "x"),
        "Extra inputs are not permitted",
    ),
]


@pytest.mark.parametrize(
    ("defect", "relative", "mutate", "message"),
    [(d, rel, m, e) for d, rels, m, e in SOW_DEFECTS for rel in rels],
    ids=[f"{d}-{rel.split('/')[-1]}" for d, rels, _, _ in SOW_DEFECTS for rel in rels],
)
def test_e0_17_t02_a_broken_sow_is_refused_with_the_documented_error(
    defect: str, relative: str, mutate: Mutation, message: str
) -> None:
    raw = load(relative)
    check_sow(raw)  # the copy is good before it is broken
    mutate(raw)
    with pytest.raises(ValidationError) as refused:
        check_sow(raw)
    assert message in str(refused.value), defect


MANIFEST_DEFECTS: list[tuple[str, str, Mutation, str]] = [
    ("missing_block", "isa14/ms01_1_sample.json", lambda r: drop(r, "contracts"), "contracts"),
    (
        "extra_top_level_block",
        "isa14/ms01_1_sample.json",
        lambda r: put(r, "sbam_build_record", {}),
        "Extra inputs are not permitted",
    ),
]


@pytest.mark.parametrize(
    ("defect", "relative", "mutate", "message"),
    MANIFEST_DEFECTS,
    ids=[d for d, *_ in MANIFEST_DEFECTS],
)
def test_e0_17_t02_a_broken_isa14_manifest_is_refused(
    defect: str, relative: str, mutate: Mutation, message: str
) -> None:
    raw = load(relative)
    mutate(raw)
    with pytest.raises(ValidationError) as refused:
        check_isa14_manifest(raw)
    assert message in str(refused.value), defect


SKILL_MANIFEST = "isa14/ms01_1_manifest.json"
SKILL_MANIFEST_DEFECTS: list[tuple[str, Mutation, str]] = [
    (
        "status_differs_from_lifecycle_state",
        lambda r: put(r, "skill_metadata.status", "DRAFT"),
        "skill_metadata.status must equal governance.lifecycle.state (C-52)",
    ),
    (
        "hash_is_not_sha256",
        lambda r: put(r, "governance.deterministic_hash", "md5:abc"),
        "governance.deterministic_hash must be 'sha256:' + 64 lowercase hex",
    ),
    (
        "skill_id_does_not_match_its_macro_category",
        lambda r: put(r, "skill_metadata.skill_id", "MS02.1_Investor_Auth_Manager"),
        "skill_id must be MSnn.m_<Name> under its macro_category",
    ),
    (
        "next_is_missing",
        lambda r: drop(r, "isatvon_definition.n_notification.next"),
        "isatvon_definition.n_notification.next is required",
    ),
    (
        "version_is_not_major_minor_patch",  # only SBAM's model checks it
        lambda r: put(r, "skill_metadata.version", "1.4"),
        "version",
    ),
    (
        "approved_manifest_must_be_immutable",
        lambda r: put(r, "governance.lifecycle.immutable_after_approval", False),
        "immutable",
    ),
]


@pytest.mark.parametrize(
    ("defect", "mutate", "message"),
    SKILL_MANIFEST_DEFECTS,
    ids=[d for d, *_ in SKILL_MANIFEST_DEFECTS],
)
def test_e0_17_t02_a_broken_skill_manifest_is_refused(
    defect: str, mutate: Mutation, message: str
) -> None:
    raw = load(SKILL_MANIFEST)
    mutate(raw)
    with pytest.raises(ValidationError) as refused:
        SkillManifest.model_validate(raw)
    assert message in str(refused.value), defect


def test_e0_17_t02_a_tampered_manifest_no_longer_matches_its_own_hash() -> None:
    raw = load(SKILL_MANIFEST)
    put(raw, "skill_metadata.skill_name", raw["skill_metadata"]["skill_name"] + " (edited)")
    SkillManifest.model_validate(raw)  # still a well-formed manifest...
    with pytest.raises(AssertionError, match="hash mismatch"):
        check_skill_manifest(raw)  # ...but its sealed hash no longer fits its content


SUB_SKILL = "isa14/ms01_1_structured.json"


def test_e0_17_t02_a_sub_skill_missing_a_required_field_breaks_rule_1() -> None:
    raw = load(SUB_SKILL)
    drop(raw, "isatvon_definition.a_automation.acceptance_check")
    with pytest.raises(RuleViolationsError) as refused:
        check_sub_skill(raw)
    assert refused.value.found == [(1, "isatvon_definition.a_automation.acceptance_check")]


def test_e0_17_t02_a_sub_skill_id_outside_its_macro_category_breaks_rule_2() -> None:
    raw = load(SUB_SKILL)
    put(raw, "skill_metadata.skill_id", "MS1.1_Auth")
    put(raw, "skill_metadata.macro_category", "MS01_X")
    with pytest.raises(RuleViolationsError) as refused:
        check_sub_skill(raw)
    assert refused.value.found == [(2, "skill_metadata.skill_id")]


def test_e0_17_the_broken_copies_never_change_the_fixtures_on_disk() -> None:
    before = {rel: fixture_path(*rel.split("/")).read_bytes() for rel in VALID}
    for _, rels, mutate, _ in SOW_DEFECTS:
        for rel in rels:
            mutate(load(rel))
    assert {rel: fixture_path(*rel.split("/")).read_bytes() for rel in VALID} == before
