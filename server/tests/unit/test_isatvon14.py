"""E0.2 unit tests: ISATVON 1.4 Agent / Sub-Skill contract and the TD §10.4 rules 1-10."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from pydantic import ValidationError
from sbam_contracts.isatvon14 import (
    Agent,
    RuleViolation,
    SubSkill,
    build_contracts,
    parse_budget_ms,
    schemas,
    validate_agent,
    validate_sub_skill,
)

SCHEMAS = Path(__file__).resolve().parents[2] / "contracts" / "schemas" / "isatvon"
IDEF = "isatvon_definition."


def sample() -> dict[str, Any]:
    raw = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def variant(**changes: Any) -> dict[str, Any]:
    """The sample with dotted-path changes, e.g. `variant(**{"skill_metadata.skill_id": "X"})`."""
    raw = sample()
    for dotted, value in changes.items():
        *parents, leaf = dotted.split(".")
        node = raw
        for part in parents:
            node = node[part]
        node[leaf] = value
    return raw


def rules_at(violations: list[RuleViolation]) -> list[tuple[int, str]]:
    return [(v.rule, v.path) for v in violations]


# --- T01 ------------------------------------------------------------------------------------------


def test_e0_2_t01_isa14_sample_validates() -> None:
    raw = sample()
    skill = SubSkill.model_validate(raw)
    assert skill.skill_metadata.skill_id == "MS01.1_Investor_Auth_Manager"
    assert validate_sub_skill(raw) == []
    assert validate_sub_skill(raw, macro_category="MS01_Investor_Registration_Access") == []


def test_e0_2_t01_loop_bounds_and_contracts_are_derived() -> None:
    skill = SubSkill.model_validate(sample())
    bounds = skill.isatvon_definition.v_variables.loop_bounds()
    assert (bounds.max_iterations, parse_budget_ms(bounds.budget_limit)) == (3, 5000)
    contracts = build_contracts(skill)
    assert contracts["input_schema"]["required"] == [
        "mobile_number",
        "otp_payload",
        "device_fingerprint",
    ]
    outputs = contracts["output_schema"]["properties"]
    assert outputs["state_handoff_id"] == {"type": "string"}
    assert outputs["auth_status"] == {
        "type": "string",
        "enum": ["AUTHENTICATED", "REJECTED", "LOCKED"],
    }


# --- Rule 1: strict structure ---------------------------------------------------------------------


def test_e0_2_rule_1_missing_required_field() -> None:
    raw = sample()
    del raw["isatvon_definition"]["a_automation"]["acceptance_check"]
    assert rules_at(validate_sub_skill(raw)) == [(1, IDEF + "a_automation.acceptance_check")]


def test_e0_2_rule_1_extra_field_and_empty_text_are_rejected() -> None:
    raw = variant(**{"skill_metadata.unexpected": "x", "skill_metadata.skill_name": "  "})
    assert set(rules_at(validate_sub_skill(raw))) == {
        (1, "skill_metadata.unexpected"),
        (1, "skill_metadata.skill_name"),
    }


def test_e0_2_rule_1_text_length_is_capped() -> None:
    raw = variant(**{IDEF + "a_automation.action_step": "x" * 4001})
    assert rules_at(validate_sub_skill(raw)) == [(1, IDEF + "a_automation.action_step")]


def test_e0_2_rule_10_control_characters_are_stripped() -> None:
    raw = variant(**{IDEF + "a_automation.action_step": "Compare\x00 OTP\x1b[31m​ now\n"})
    skill = SubSkill.model_validate(raw)
    assert skill.isatvon_definition.a_automation.action_step == "Compare OTP[31m now\n"


def test_e0_2_empty_state_and_tables_are_legitimate() -> None:
    raw = variant(
        **{
            IDEF + "s_source.fresh_state_parameters": [],
            IDEF + "v_variables.no_op_condition": "auth_status is 'LOCKED'",
            IDEF + "v_variables.escalation_condition": "otp_match == False",
            IDEF + "v_variables.tables": [],
        }
    )
    SubSkill.model_validate(raw)
    assert rules_at(validate_sub_skill(raw)) == [(7, IDEF + "o_outcome.state_handoff_file.table")]


def test_e0_2_external_data_kind_accepts_json_strings() -> None:
    sources = [{"name": "kyc_api", "kind": "API", "ref": "https://example.com/kyc"}]
    raw = variant(**{IDEF + "s_source.external_data": sources})
    assert validate_sub_skill(raw) == []
    sources[0]["kind"] = "FTP"
    assert rules_at(validate_sub_skill(raw)) == [(1, IDEF + "s_source.external_data.0.kind")]


# --- Rule 2: IDs (T02) ----------------------------------------------------------------------------


@pytest.mark.parametrize("skill_id", ["MS1.1_Auth", "MS02.1_Auth"])
def test_e0_2_t02_bad_skill_id_or_prefix_mismatch(skill_id: str) -> None:
    raw = variant(
        **{"skill_metadata.skill_id": skill_id, "skill_metadata.macro_category": "MS01_X"}
    )
    assert rules_at(validate_sub_skill(raw)) == [(2, "skill_metadata.skill_id")]


def test_e0_2_rule_2_macro_category_and_master_refs() -> None:
    raw = variant(
        **{
            "skill_metadata.macro_category": "MS01-Investor",
            "skill_metadata.skill_ref": "skill-1",
            "skill_metadata.rule_refs": ["RULE-BOM", "R1"],
        }
    )
    assert rules_at(validate_sub_skill(raw)) == [
        (2, "skill_metadata.macro_category"),
        (2, "skill_metadata.skill_ref"),
        (2, "skill_metadata.rule_refs.1"),
    ]


# --- Rule 3: I/A consistency ----------------------------------------------------------------------


def test_e0_2_rule_3_bound_and_statement_must_agree_with_v() -> None:
    raw = variant(
        **{
            IDEF + "i_instructions.bound": "Allow a few retries",
            IDEF + "i_instructions.statement": "Validate the OTP. Check: it matches.",
        }
    )
    assert rules_at(validate_sub_skill(raw)) == [
        (3, IDEF + "i_instructions.statement"),
        (3, IDEF + "i_instructions.statement"),
        (3, IDEF + "i_instructions.statement"),
        (3, IDEF + "i_instructions.bound"),
    ]


# --- Rule 4: decoupling (T05) ---------------------------------------------------------------------


def test_e0_2_t05_action_tool_must_differ_from_fixed_check_tool() -> None:
    raw = variant(
        **{
            IDEF + "t_tech_stack.action_tool": "auth_tool",
            IDEF + "t_tech_stack.fixed_check_tool": "auth_tool",
        }
    )
    assert rules_at(validate_sub_skill(raw)) == [(4, IDEF + "t_tech_stack.fixed_check_tool")]


# --- Rule 5: loop bounds (T03, T04, T06, T07) -----------------------------------------------------


@pytest.mark.parametrize("value", [1, 10])
def test_e0_2_t03_max_iterations_boundaries_valid(value: int) -> None:
    raw = variant(
        **{
            IDEF + "v_variables.max_iterations": value,
            IDEF + "i_instructions.bound": f"Allow up to {value} OTP retry loops",
        }
    )
    assert validate_sub_skill(raw) == []


@pytest.mark.parametrize("value", [0, 11])
def test_e0_2_t03_max_iterations_out_of_range_fails_rule_5(value: int) -> None:
    raw = variant(
        **{
            IDEF + "v_variables.max_iterations": value,
            IDEF + "i_instructions.bound": f"Allow up to {value} OTP retry loops",
        }
    )
    assert rules_at(validate_sub_skill(raw)) == [(5, IDEF + "v_variables.max_iterations")]


def test_e0_2_t03_max_iterations_string_fails_strict_type() -> None:
    raw = variant(**{IDEF + "v_variables.max_iterations": "3"})
    assert rules_at(validate_sub_skill(raw)) == [(1, IDEF + "v_variables.max_iterations")]


@pytest.mark.parametrize(
    ("text", "ms"), [("500ms", 500), ("2s", 2000), ("500ms per cycle", 500), (" 2 s ", 2000)]
)
def test_e0_2_t04_budget_limit_parses(text: str, ms: int) -> None:
    assert parse_budget_ms(text) == ms


@pytest.mark.parametrize("text", ["0ms", "abc", "5m", "500msx", "ms"])
def test_e0_2_t04_bad_budget_limit_fails_rule_5(text: str) -> None:
    with pytest.raises(ValueError):
        parse_budget_ms(text)
    raw = variant(**{IDEF + "v_variables.budget_limit": text})
    assert rules_at(validate_sub_skill(raw)) == [(5, IDEF + "v_variables.budget_limit")]


@pytest.mark.parametrize(
    ("gate", "code"),
    [
        ("__import__('os').system('id')", "CALL_NOT_ALLOWED"),
        ("a.b == 1", "ATTRIBUTE_NOT_ALLOWED"),
        ("x[0] == 1", "SUBSCRIPT_NOT_ALLOWED"),
        ("f(x)", "CALL_NOT_ALLOWED"),
    ],
)
def test_e0_2_t06_expression_injection_fails_rule_5(gate: str, code: str) -> None:
    violations = validate_sub_skill(variant(**{IDEF + "v_variables.success_gate": gate}))
    assert rules_at(violations) == [(5, IDEF + "v_variables.success_gate")]
    assert code in violations[0].message


def test_e0_2_t07_unresolved_identifier_is_named() -> None:
    gate = 'auth_status == "OK" AND unknown_x > 1'
    violations = validate_sub_skill(variant(**{IDEF + "v_variables.success_gate": gate}))
    assert rules_at(violations) == [(5, IDEF + "v_variables.success_gate")]
    assert "'unknown_x'" in violations[0].message


def test_e0_2_rule_5_keyword_named_field_cannot_be_referenced() -> None:
    raw = variant(**{IDEF + "v_variables.no_op_condition": "is == 1"})
    assert rules_at(validate_sub_skill(raw)) == [(5, IDEF + "v_variables.no_op_condition")]


# --- Rule 6: contracts ----------------------------------------------------------------------------


def test_e0_2_rule_6_inputs_must_be_unique_field_names() -> None:
    raw = variant(
        **{IDEF + "s_source.required_inputs": ["mobile number", "otp_payload", "otp_payload"]}
    )
    raw[IDEF[:-1]]["v_variables"]["success_gate"] = "otp_match == True"
    assert rules_at(validate_sub_skill(raw)) == [
        (6, IDEF + "s_source.required_inputs.0"),
        (6, IDEF + "s_source.required_inputs"),
    ]


def test_e0_2_rule_6_json_schema_must_convert() -> None:
    raw = variant(**{IDEF + "o_outcome.json_schema.user_role": 42})
    assert rules_at(validate_sub_skill(raw)) == [(6, IDEF + "o_outcome.json_schema")]


# --- Rule 7: state handoff (T08) ------------------------------------------------------------------


def test_e0_2_t08_state_handoff_rules() -> None:
    handoff = IDEF + "o_outcome.state_handoff_file."
    raw = variant(
        **{
            handoff + "mode": "upsert",
            handoff + "table": "not_listed",
            handoff + "required_fields": [],
        }
    )
    assert rules_at(validate_sub_skill(raw)) == [
        (7, handoff + "mode"),
        (7, handoff + "table"),
        (7, handoff + "required_fields"),
    ]


# --- Rule 8: notifications + next (T09) -----------------------------------------------------------


def test_e0_2_t09_notifications_minimum() -> None:
    violations = validate_sub_skill(variant(**{IDEF + "n_notification.ui_updates": ["Done"]}))
    assert rules_at(violations) == [(8, IDEF + "n_notification.ui_updates")]
    assert "at least 2" in violations[0].message
    assert "Looping [Pass x/y]" in violations[0].message


@pytest.mark.parametrize("target", ["MS01.2_Investor_KYC_Verifier", "MS02_Portfolio_Access", None])
def test_e0_2_rule_8_next_may_name_a_sub_skill_an_agent_or_null(target: str | None) -> None:
    assert validate_sub_skill(variant(**{IDEF + "n_notification.next": target})) == []


@pytest.mark.parametrize(
    "target", ["KYC step", "MS01.1_Investor_Auth_Manager", "MS01_Investor_Registration_Access"]
)
def test_e0_2_rule_8_next_must_be_an_id_and_not_itself(target: str) -> None:
    raw = variant(**{IDEF + "n_notification.next": target})
    assert rules_at(validate_sub_skill(raw)) == [(8, IDEF + "n_notification.next")]


def test_e0_2_rule_8_next_is_required_even_when_null() -> None:
    raw = sample()
    del raw["isatvon_definition"]["n_notification"]["next"]
    assert rules_at(validate_sub_skill(raw)) == [(1, IDEF + "n_notification.next")]


# --- Rule 9: libraries ----------------------------------------------------------------------------


def test_e0_2_rule_9_libraries_must_be_registered_and_not_duckdb() -> None:
    libs = [{"package": "DuckDB"}, {"package": "left-pad"}, {"package": "Psycopg"}]
    violations = validate_sub_skill(variant(**{IDEF + "t_tech_stack.libraries": libs}))
    assert rules_at(violations) == [
        (9, IDEF + "t_tech_stack.libraries.0.package"),
        (9, IDEF + "t_tech_stack.libraries.1.package"),
    ]
    assert "PostgreSQL only" in violations[0].message


def test_e0_2_rule_9_registry_is_injectable() -> None:
    assert validate_sub_skill(sample(), library_registry={"fastmcp", "psycopg", "pyjwt"}) == []
    assert len(validate_sub_skill(sample(), library_registry={"fastmcp"})) == 2


# --- Rule 10: secrets and traceability ------------------------------------------------------------


@pytest.mark.parametrize(
    "secret", ["sk-live-1234567890abcdef", "-----BEGIN PRIVATE KEY-----", "A" * 48, "my key"]
)
def test_e0_2_rule_10_secret_values_are_rejected(secret: str) -> None:
    raw = variant(**{IDEF + "v_variables.secrets": ["AUTH_VAULT_KEY", secret]})
    assert rules_at(validate_sub_skill(raw)) == [(10, IDEF + "v_variables.secrets.1")]


# --- Agents and the next chain --------------------------------------------------------------------

MS01 = "MS01_Investor_Registration_Access"
MS02 = "MS02_Portfolio_Access"


def sub(skill_id: str, nxt: str | None) -> dict[str, Any]:
    return variant(**{"skill_metadata.skill_id": skill_id, IDEF + "n_notification.next": nxt})


def agent(*skills: dict[str, Any], **changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "macro_category": MS01,
        "name": "Investor Registration & Access",
        "description": "Sign-up, login and device control for investors",
        "order": 1,
        "sow_refs": ["SOW-4.1"],
        "agent_ref": "AG-AUTH",
        "sub_skills": list(skills),
    }
    raw.update(changes)
    return raw


A, B, C = "MS01.1_Auth", "MS01.2_Kyc", "MS01.3_Welcome"


def test_e0_2_agent_with_linear_chain_validates() -> None:
    raw = agent(sub(A, B), sub(B, C), sub(C, MS02))
    Agent.model_validate(raw)
    assert validate_agent(raw) == []
    assert validate_agent(raw, workflow_agents=[MS01, MS02]) == []
    # Listed out of order is fine: the chain, not the list, defines the order.
    assert validate_agent(agent(sub(C, MS02), sub(A, B), sub(B, C))) == []


def test_e0_2_last_agent_ends_with_null() -> None:
    raw = agent(sub(A, B), sub(B, None))
    assert validate_agent(raw, workflow_agents=[MS01]) == []
    assert rules_at(validate_agent(raw, workflow_agents=[MS01, MS02])) == [
        (8, "sub_skills.1.isatvon_definition.n_notification.next")
    ]


def test_e0_2_last_sub_skill_must_point_to_the_following_agent() -> None:
    raw = agent(sub(A, "MS03_Reports"))
    violations = validate_agent(raw, workflow_agents=[MS01, MS02, "MS03_Reports"])
    assert rules_at(violations) == [(8, "sub_skills.0.isatvon_definition.n_notification.next")]
    assert repr(MS02) in violations[0].message


@pytest.mark.parametrize(
    ("skills", "bad"),
    [
        ([(A, B), (B, A)], 0),  # cycle, no start
        ([(A, C), (B, C), (C, None)], 2),  # branch: two point at C
        ([(A, None), (B, None)], 0),  # two chains
        ([(A, "MS02.1_Other"), (B, None)], 0),  # another Agent's Sub-Skill
        ([(A, "MS01.9_Missing"), (B, None)], 0),  # unknown Sub-Skill
        ([(A, B), (B, MS01)], 1),  # points to its own Agent
        ([(A, B), (B, C), (C, B)], 1),  # loop back: A and C both point at B
        ([(A, None), (B, C), (C, B)], 1),  # B and C loop on their own, unreachable from A
    ],
)
def test_e0_2_rule_8_broken_next_chain(skills: list[tuple[str, str | None]], bad: int) -> None:
    violations = validate_agent(agent(*(sub(s, n) for s, n in skills)))
    assert violations, skills
    assert {v.rule for v in violations} == {8}
    assert violations[0].path == f"sub_skills.{bad}.isatvon_definition.n_notification.next"


def test_e0_2_agent_rules_2_and_10() -> None:
    raw = agent(sub(A, None), sub(A, None), macro_category=MS01, agent_ref="agent-1", sow_refs=[])
    found = rules_at(validate_agent(raw))
    assert (2, "agent_ref") in found
    assert (10, "sow_refs") in found
    assert (2, "sub_skills.1.skill_metadata.skill_id") in found


def test_e0_2_agent_sub_skill_must_belong_to_the_agent() -> None:
    raw = agent(sub("MS02.1_Auth", None))
    found = rules_at(validate_agent(raw))
    assert (2, "sub_skills.0.skill_metadata.skill_id") in found


def test_e0_2_agent_needs_at_least_one_sub_skill() -> None:
    assert rules_at(validate_agent(agent())) == [(1, "sub_skills")]


def test_e0_2_models_are_strict_and_frozen() -> None:
    skill = SubSkill.model_validate(sample())
    with pytest.raises(ValidationError):
        skill.skill_metadata.skill_id = "MS09.9_X"
    raw = copy.deepcopy(sample())
    raw["isatvon_definition"]["n_notification"]["alerts"] = "one alert"
    with pytest.raises(ValidationError):
        SubSkill.model_validate(raw)


# --- Committed JSON Schemas -----------------------------------------------------------------------


@pytest.mark.parametrize("stem", sorted(schemas()))
def test_e0_2_committed_schema_matches_model(stem: str) -> None:
    committed = json.loads((SCHEMAS / f"{stem}.schema.json").read_text(encoding="utf-8"))
    assert (
        committed == schemas()[stem]
    ), "regenerate: cd server && uv run python -m sbam_contracts.isatvon14"
