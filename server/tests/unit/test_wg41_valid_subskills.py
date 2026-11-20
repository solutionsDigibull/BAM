"""WG-41 (#929): the real model's Sub-Skills came out INVALID (no_op_condition on an output or an
undeclared name, no Action/Check/Bound, `float`/`String`/`Priced` as JSON-schema types).

`isatvon-sub-skills` 1.6.0 teaches each rule with right and wrong examples, and a Sub-Skill that
still fails the build's validator is asked again with the code, the position, the offending names
and one fixed sentence (`sbam_workers.isatvon.feedback`), at most `RULE_REASKS` times. Everything
here is SYNTHETIC (`server/evals/golden/isatvon-invalid-subskills.json`) and calls no model.
"""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from knowledge_db import World
from llm_db import FakeBifrost, completion
from sbam_contracts import gate_expr
from sbam_contracts.isatvon14 import (
    DEFAULT_LIBRARY_REGISTRY,
    build_contracts,
    validate_sub_skill,
)
from sbam_contracts.isatvon14 import (
    SubSkill as SubSkillModel,
)
from sbam_workers.isatvon import feedback
from sbam_workers.isatvon.sub_skills import RULE_REASKS
from sbam_workers.llm.registry import PromptRegistry
from sub_skills_db import Pass2, answer, proposal, sample

IDEF = "isatvon_definition."
MACRO = "MS01_Investor_Registration_Access"
AUTH = proposal("Investor Auth Manager", "F-ACCESS-1")
KYC = proposal("Investor KYC Verifier", "F-ACCESS-2")
GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[2] / "evals" / "golden" / "isatvon-invalid-subskills.json"
    ).read_text(encoding="utf-8")
)
CASES: dict[str, dict[str, Any]] = {c["id"]: c for c in GOLDEN["cases"]}
PROMPT = PromptRegistry().get("isatvon-sub-skills")


@pytest.fixture
def p(tmp_path: Path) -> Iterator[Pass2]:
    yield Pass2(World(tmp_path))


def changed(changes: dict[str, Any]) -> dict[str, Any]:
    raw = copy.deepcopy(sample())
    for path, value in changes.items():
        *parents, leaf = path.split(".")
        node = raw
        for part in parents:
            node = node[part]
        node[leaf] = value
    return raw


def findings(raw: dict[str, Any]) -> list[dict[str, Any]]:
    found = validate_sub_skill(raw, macro_category=MACRO, library_registry=DEFAULT_LIBRARY_REGISTRY)
    return [f.model_dump(mode="json") for f in found]


def lines_for(raw: dict[str, Any]) -> list[str]:
    return feedback.lines([raw], [findings(raw)])


# --- the prompt ----------------------------------------------------------------------------------


def test_wg41_t01_the_prompt_is_1_6_0_and_states_the_four_parts_with_an_example_of_each() -> None:
    assert PROMPT.version == "1.6.0"
    for part in ("Triggered by", "Action:", "Check:", "Bound:"):
        assert f"`{part}" in PROMPT.text or f"`{part} " in PROMPT.text
    assert "ALL FOUR parts" in PROMPT.text
    example = re.search(r"A complete statement: `([^`]+)`", PROMPT.text.replace("\n", " "))
    assert example is not None
    shown = re.sub(r"\s+", " ", example.group(1))
    for part in ("Triggered by", "Action:", "Check:", "Bound:"):
        assert part in shown


NO_OP_RIGHT = re.findall(r"No-op right: `([^`]+)`", PROMPT.text)
NO_OP_WRONG = re.findall(r"No-op wrong: `([^`]+)`", PROMPT.text)


def with_no_op(text: str) -> dict[str, Any]:
    raw = changed({IDEF + "v_variables.no_op_condition": text})
    raw["isatvon_definition"]["s_source"]["required_inputs"].append("already_sent")
    return raw


def no_op_findings(text: str) -> list[dict[str, Any]]:
    return [f for f in findings(with_no_op(text)) if f["path"].endswith("no_op_condition")]


def test_wg41_t02_the_prompt_gives_no_op_examples_and_says_what_is_not_allowed() -> None:
    assert len(NO_OP_RIGHT) >= 3 and len(NO_OP_WRONG) >= 3
    assert "BEFORE the first pass" in PROMPT.text
    assert "Never an output field" in PROMPT.text and "undeclared name" in PROMPT.text


@pytest.mark.parametrize("text", NO_OP_RIGHT)
def test_wg41_t02_every_right_no_op_example_validates(text: str) -> None:
    gate_expr.parse(text)
    assert no_op_findings(text) == []


@pytest.mark.parametrize("text", NO_OP_WRONG)
def test_wg41_t02_every_wrong_no_op_example_is_refused(text: str) -> None:
    # `status` is not an output of the MS01.1 sample: the wrong examples are refused either as an
    # output (the prompt's own `status` field) or as an undeclared name; either way by the code.
    raw = with_no_op(text)
    raw["isatvon_definition"]["o_outcome"]["json_schema"]["status"] = "string"
    refused = [
        f
        for f in findings(raw)
        if f["path"].endswith("no_op_condition") and f["code"] == "UNRESOLVED_IDENTIFIER"
    ]
    assert refused


def test_wg41_t03_the_prompt_names_the_allowed_types_and_forbids_the_wrong_ones() -> None:
    for name in feedback.TYPE_NAMES:
        assert f"`{name}`" in PROMPT.text
    for wrong in ("float", "String", "Priced"):
        assert wrong in PROMPT.text.split("Never `float`")[1].split("Sub-Skill is refused")[0]


@pytest.mark.parametrize("name", feedback.TYPE_NAMES)
def test_wg41_t03_every_allowed_type_name_converts(name: str) -> None:
    raw = changed({IDEF + "o_outcome.json_schema.user_role": name})
    build_contracts(SubSkillModel.model_validate(raw))
    assert not [f for f in findings(raw) if f["path"].endswith("o_outcome.json_schema")]


@pytest.mark.parametrize("name", ["float", "String", "Priced", "int", "list", "positive_float"])
def test_wg41_t03_the_types_the_real_model_wrote_are_refused(name: str) -> None:
    raw = changed({IDEF + "o_outcome.json_schema.user_role": name})
    assert [f for f in findings(raw) if f["path"].endswith("o_outcome.json_schema")]


def test_wg41_t03_the_prompt_version_1_5_0_is_kept_unchanged() -> None:
    old = PromptRegistry().get("isatvon-sub-skills", "1.5.0")
    assert old.version == "1.5.0" and "ALL FOUR parts" not in old.text


# --- the golden cases and the feedback ---------------------------------------------------------


@pytest.mark.parametrize("case_id", list(CASES))
def test_wg41_t04_the_validator_refuses_each_golden_case_with_its_code(case_id: str) -> None:
    case = CASES[case_id]
    found = findings(changed(case["changes"]))
    assert (case["path"], case["code"]) in {(f["path"], f["code"]) for f in found}


@pytest.mark.parametrize("case_id", list(CASES))
def test_wg41_t05_the_feedback_quotes_the_code_the_position_and_the_names(case_id: str) -> None:
    case = CASES[case_id]
    text = "\n".join(lines_for(changed(case["changes"])))
    assert f"sub_skills[0].sub_skill.{case['path']}: {case['code']}" in text
    for expected in case["feedback"]:
        assert expected in text, expected
    for never in case.get("never", []):
        assert never not in text


def test_wg41_t05_the_feedback_never_carries_the_validator_message_or_document_text() -> None:
    key = "sk-" + "or-v1-" + "abc1234567890abcdef"  # built at run time, never a literal
    raw = changed(
        {
            IDEF + "o_outcome.json_schema.user_role": "ZZ-DOCUMENT-TEXT",
            IDEF + "v_variables.secrets": ["AUTH_VAULT_KEY", key],
        }
    )
    text = "\n".join(lines_for(raw))
    assert "ZZ-DOCUMENT-TEXT" not in text and "or-v1-" not in text
    assert "SECRET_VALUE_NOT_ALLOWED" not in text  # a stored finding, never asked again
    assert "cannot convert" not in text  # the validator's own message stays out


def test_wg41_t05_a_valid_sub_skill_gets_no_feedback_and_ids_set_by_sbam_are_not_asked() -> None:
    assert lines_for(sample()) == []
    raw = changed({"skill_metadata.skill_id": "MS03_1"})
    assert lines_for(raw) == []  # SBAM sets the ids


def test_wg41_t05_a_name_that_is_not_a_plain_identifier_is_never_quoted() -> None:
    finding = {
        "code": "UNRESOLVED_IDENTIFIER",
        "path": IDEF + "v_variables.no_op_condition",
        "message": "unresolved identifier 'Quote 12 cables for ACME'",
        "rule": 5,
    }
    text = feedback.explain(sample(), finding)
    assert "ACME" not in text and "UNRESOLVED_IDENTIFIER rule 5" in text


def test_wg41_t06_the_regenerate_feedback_carries_the_same_detail() -> None:
    raw = changed({IDEF + "v_variables.no_op_condition": "auth_status == 'LOCKED'"})
    (stored,) = [f for f in findings(raw) if f["path"].endswith("no_op_condition")]
    detail = feedback.explain(raw, stored)
    assert "auth_status (an output)" in detail and "max_iterations < 1" in detail


# --- the re-ask, end to end on a fake gateway ---------------------------------------------


def rfq_style_invalid() -> dict[str, Any]:
    """The classes of the live run in one Sub-Skill: a no-op on an output, a statement without
    Check and Bound, and a type that is not a JSON-schema type."""
    return proposal(
        "Investor Auth Manager",
        "F-ACCESS-1",
        **{
            IDEF + "v_variables.no_op_condition": "auth_status == 'LOCKED'",
            IDEF + "i_instructions.statement": "Triggered by login. Action: Validate the OTP.",
            IDEF + "o_outcome.json_schema.user_role": "float",
        },
    )


def test_wg41_t07_an_invalid_first_answer_is_fixed_on_the_second_attempt(p: Pass2) -> None:
    first = completion(answer(rfq_style_invalid(), KYC))
    bifrost = FakeBifrost([first, completion(answer(AUTH, KYC))])
    ids = p.run_macro(bifrost)
    assert len(ids) == 2 and len(bifrost.requests) == 2
    assert [r.state for r in p.sub_skills()] == ["VALID", "VALID"]
    sent = bifrost.requests[1]["messages"][-1]["content"]
    assert "sub_skills[0].sub_skill.isatvon_definition.v_variables.no_op_condition" in sent
    assert "auth_status (an output)" in sent
    assert "missing Check:" in sent and "missing Bound:" in sent
    assert "properties user_role" in sent
    assert "sub_skills[1]" not in sent  # the valid one is not mentioned


def test_wg41_t08_a_sub_skill_that_stays_invalid_is_stored_after_the_rule_re_asks(
    p: Pass2,
) -> None:
    bifrost = FakeBifrost([completion(answer(rfq_style_invalid(), KYC))])
    p.run_macro(bifrost)
    assert len(bifrost.requests) == 1 + RULE_REASKS  # then taken as it is, never refused
    first, second = p.sub_skills()
    assert (first.state, second.state) == ("INVALID", "VALID")
    assert {e["code"] for e in first.validation_errors} == {
        "UNRESOLVED_IDENTIFIER",
        "ISATVON_CONTRACT",
    }


def test_wg41_t09_a_coverage_error_is_asked_before_the_rules(p: Pass2) -> None:
    bifrost = FakeBifrost([completion(answer(rfq_style_invalid())), completion(answer(AUTH, KYC))])
    p.run_macro(bifrost)
    first = bifrost.requests[1]["messages"][-1]["content"]
    assert "FEATURE_NOT_COVERED" in first and "no_op_condition" not in first
    assert [r.state for r in p.sub_skills()] == ["VALID", "VALID"]


def test_wg41_t10_the_eval_gate_lists_the_golden_cases_and_scores_none() -> None:
    root = Path(__file__).resolve().parents[2] / "evals"
    tasks = {t["task"]: t for t in json.loads((root / "golden" / "tasks.json").read_text("utf-8"))}
    assert tasks["isatvon"]["status"] == "todo"  # no real-model baseline: it needs a spend cap
    assert "isatvon-invalid-subskills.json" in (root / "README.md").read_text("utf-8")
    assert not (root / "baselines" / "isatvon.json").exists()
    assert len(GOLDEN["cases"]) >= 10
