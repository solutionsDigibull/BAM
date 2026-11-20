"""WG-40 (#928): a fresh-state name codegen cannot query is refused at the ISATVON stage.

The live run of 2026-11-20 had a Sub-Skill that declared `fresh_state_parameters: ["attempt"]` (the
old prompt taught it). The ISATVON validator accepted it; codegen then needed a state query on a
column `attempt` that no table has, and the corrector could neither remove the name (it deleted the
required key) nor repair the gates. The fixture here is SYNTHETIC with the same shape: no recorded
customer content.
"""

import json
import re
from typing import Any

import pytest
from fixtures import fixture_path
from sbam_contracts.build import IsatvonPatchClass
from sbam_contracts.isatvon14 import validate_sub_skill
from sbam_workers.build.codegen.bodies import BodyFindingCode, _check_state_queries
from sbam_workers.build.codegen.correction import (
    PatchFindingCode,
    PatchOp,
    apply_isatvon_patch,
    classify_patch,
    recompile,
)
from sbam_workers.llm.registry import PromptRegistry

S = "/isatvon_definition/s_source/fresh_state_parameters"
V = "/isatvon_definition/v_variables/"
SCHEMA = """
CREATE TABLE order_requests (
  id uuid PRIMARY KEY, order_id text, status text, missing_count integer, payload jsonb
);
"""
TABLES = ["order_requests"]


def sample() -> dict[str, Any]:
    raw = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def recorded_shape() -> dict[str, Any]:
    """The shape of the failing output: `attempt` is fresh state and a gate counts on it."""
    raw = sample()
    definition = raw["isatvon_definition"]
    definition["s_source"]["fresh_state_parameters"] = ["attempt"]
    definition["v_variables"]["escalation_condition"] = "attempt >= max_iterations"
    return raw


def messages(raw: dict[str, Any]) -> list[str]:
    return [v.message for v in validate_sub_skill(raw)]


def test_wg40_t01_the_recorded_shape_is_refused_at_the_validator_with_the_reason() -> None:
    found = validate_sub_skill(recorded_shape())
    counter = [v for v in found if v.path.endswith("s_source.fresh_state_parameters.0")]
    assert len(counter) == 1 and counter[0].rule == 6
    assert "'attempt'" in counter[0].message and "loop counter" in counter[0].message
    assert "[] is valid" in counter[0].message


@pytest.mark.parametrize("name", ["attempt", "Attempts", "retry_count", "iteration", "pass"])
def test_wg40_t01b_every_loop_counter_name_is_refused(name: str) -> None:
    raw = sample()
    raw["isatvon_definition"]["s_source"]["fresh_state_parameters"] = [name]
    assert any("loop counter" in m for m in messages(raw))


def test_wg40_t02_a_corrected_isatvon_validates_and_passes_the_state_query_check() -> None:
    raw = recorded_shape()
    raw["isatvon_definition"]["s_source"]["fresh_state_parameters"] = []
    raw["isatvon_definition"]["v_variables"]["escalation_condition"] = "policy_violation == True"
    raw["isatvon_definition"]["v_variables"]["no_op_condition"] = "max_iterations > 99"
    assert validate_sub_skill(raw) == [], messages(raw)
    # no fresh state: no queries, nothing for the pre-check to reject
    assert _check_state_queries({}, [], TABLES, hint=True, schema_sql=SCHEMA) == []


def test_wg40_t03_the_recorded_pre_check_failure_is_still_what_codegen_reports() -> None:
    queries = {
        "attempt": "SELECT attempt FROM order_requests WHERE order_id = %s",
    }
    found = _check_state_queries(queries, ["attempt"], TABLES, hint=True, schema_sql=SCHEMA)
    assert [f.code for f in found] == [BodyFindingCode.STATE_QUERY_INVALID]
    assert "no column attempt" in found[0].message
    other = {"attempt": "SELECT n FROM attempts_table WHERE order_id = %s"}
    found = _check_state_queries(other, ["attempt"], TABLES, hint=True, schema_sql=SCHEMA)
    assert any(
        "'attempts_table' is not one of the workflow's relations" in f.message for f in found
    )


def test_wg40_t04_the_corrector_repairs_the_class_with_a_patch_the_contract_allows() -> None:
    raw = recorded_shape()
    ops = [
        PatchOp(op="replace", path=S, value=[]),
        PatchOp(op="replace", path=V + "escalation_condition", value="policy_violation == True"),
        PatchOp(op="replace", path=V + "no_op_condition", value="max_iterations > 99"),
    ]
    patched, problems = apply_isatvon_patch(raw, ops)
    assert problems == [] and patched is not None
    assert patched["isatvon_definition"]["s_source"]["fresh_state_parameters"] == []
    spec, broken = recompile(patched)
    assert broken == [] and spec is not None
    # it changes what the Sub-Skill does: the FDE decides (held patch), the corrector never loops
    assert classify_patch(ops)[0] is IsatvonPatchClass.BEHAVIOUR
    assert raw["isatvon_definition"]["s_source"]["fresh_state_parameters"] == ["attempt"]


def test_wg40_t05_removing_the_required_key_gets_a_precise_hint_not_a_loop() -> None:
    patched, problems = apply_isatvon_patch(
        recorded_shape(),
        [PatchOp(op="remove", path="/isatvon_definition/s_source/fresh_state_parameters")],
    )
    assert patched is None
    assert problems and problems[0].code is PatchFindingCode.ISATVON_CONTRACT
    assert "replace" in problems[0].message and "[]" in problems[0].message


def test_wg40_t06_a_pointer_that_misses_names_the_members_that_exist() -> None:
    _, problems = apply_isatvon_patch(
        recorded_shape(), [PatchOp(op="remove", path="/isatvon_definition/s_source/attempt")]
    )
    assert problems[0].code is PatchFindingCode.PATCH_INVALID
    assert "no member 'attempt'" in problems[0].message
    assert "fresh_state_parameters" in problems[0].message  # the member to aim at


def test_wg40_t07_the_key_stays_required_and_an_empty_list_is_valid() -> None:
    raw = sample()
    raw["isatvon_definition"]["s_source"]["fresh_state_parameters"] = []
    raw["isatvon_definition"]["v_variables"]["escalation_condition"] = "max_iterations > 99"
    raw["isatvon_definition"]["v_variables"]["no_op_condition"] = "max_iterations > 99"
    assert validate_sub_skill(raw) == []
    del raw["isatvon_definition"]["s_source"]["fresh_state_parameters"]
    assert any("fresh_state_parameters" in v.path for v in validate_sub_skill(raw))


def test_wg40_t08_old_valid_isatvons_are_unchanged() -> None:
    assert (
        validate_sub_skill(sample()) == []
    )  # failed_login_attempts is a column name, not a counter
    raw = sample()
    raw["isatvon_definition"]["s_source"]["fresh_state_parameters"] += [
        "attempt_log",
        "retry_after",
    ]
    assert not any("loop counter" in m for m in messages(raw))
    manifest = json.loads(fixture_path("isa14", "ms01_1_manifest.json").read_text(encoding="utf-8"))
    names = manifest["sbam_build_record"]["isatvon_detail"]["s_source"]["fresh_state_parameters"]
    assert not {n.lower() for n in names} & {"attempt", "attempts", "iteration"}


def test_wg40_t09_the_prompts_say_it() -> None:
    registry = PromptRegistry()
    isatvon = registry.get("isatvon-sub-skills")
    assert isatvon.version == "1.5.0"
    assert "Never a loop counter" in isatvon.text and "`[]`, which is valid" in isatvon.text
    assert not re.search(r"list `attempt` in `fresh_state_parameters`", isatvon.text)
    assert "attempt" not in re.search(r"Declared here[^\n]*\n[^\n]*", isatvon.text).group(0)  # type: ignore[union-attr]
    assert "list `attempt`" in registry.get("isatvon-sub-skills", "1.4.0").text  # history kept
    corrector = registry.get("correct-code")
    assert corrector.version == "1.4.0" and "Never `remove` that key" in corrector.text
