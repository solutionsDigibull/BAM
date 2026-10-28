"""E8.3 ISATVON pass 2 (`extract_for_macro`): Macro Skills stored by pass 1, an approved SOW, a fake
Bifrost, SQLite. The model's answers are built from the ISA14 MS01.1 sample; SBAM's ids, chain,
redaction and TD §10.4 findings are real."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from knowledge_db import World
from llm_db import FakeBifrost, completion
from sbam_api.audit.models import AuditLog
from sbam_api.llm.models import LlmCall
from sbam_workers.isatvon.sub_skills import (
    EXTRACT_ATTEMPTS,
    FEATURE_NOT_COVERED,
    LLM_UNAVAILABLE,
    REDACTED,
    SCHEMA_INVALID,
    SOW_NOT_APPROVED,
    skill_ids,
)
from sbam_workers.llm.reask import MAX_REASKS
from sbam_workers.pipeline.project_pipeline import PLAN
from sqlalchemy import select
from sub_skills_db import Pass2, answer, proposal, sample
from temporalio.exceptions import ApplicationError

IDEF = "isatvon_definition."
MACRO = "MS01_Investor_Registration_Access"
AUTH = proposal("Investor Auth Manager", "F-ACCESS-1")
KYC = proposal("Investor KYC Verifier", "F-ACCESS-2")


@pytest.fixture
def p(tmp_path: Path) -> Iterator[Pass2]:
    yield Pass2(World(tmp_path))


def fails(p: Pass2, bifrost: FakeBifrost, **kw: Any) -> ApplicationError:
    with pytest.raises(ApplicationError) as err:
        p.run_macro(bifrost, **kw)
    assert err.value.non_retryable
    return err.value


def codes(row: Any) -> list[tuple[str, str]]:
    return [(e["path"], e["code"]) for e in row.validation_errors]


def test_e8_3_t01_the_ms01_1_equivalent_comes_out_valid(p: Pass2) -> None:
    bifrost = FakeBifrost([completion(answer(AUTH, KYC))])
    ids = p.run_macro(bifrost)
    first, second = p.sub_skills()
    assert [first.id, second.id] == ids
    assert (first.skill_id, second.skill_id) == (
        "MS01.1_Investor_Auth_Manager",
        "MS01.2_Investor_KYC_Verifier",
    )
    assert (first.state, second.state) == ("VALID", "VALID")
    assert first.validation_errors == []
    golden = sample()
    # Structurally the ISA14 sample: the same elements, five bounds, append-only handoff, and the
    # same `next` (SBAM linked it to the following Sub-Skill).
    assert first.isatvon["isatvon_definition"] == golden["isatvon_definition"]
    meta = first.isatvon["skill_metadata"]
    assert (meta["macro_category"], meta["skill_id"]) == (
        MACRO,
        golden["skill_metadata"]["skill_id"],
    )
    assert second.isatvon["isatvon_definition"]["n_notification"]["next"] is None  # last of all
    assert (first.order, second.order) == (1, 2)
    assert (first.sow_refs, second.sow_refs) == (["F-ACCESS-1"], ["F-ACCESS-2"])
    assert first.isatvon_framework_version == "1.4"
    assert len(first.content_sha256) == 64
    request = bifrost.requests[0]
    assert request["model"] == "sbam-isatvon"
    assert request["metadata"]["prompt_id"] == "isatvon-sub-skills"


def test_e8_3_each_row_is_created_draft_then_validated(p: Pass2) -> None:
    p.run_macro(FakeBifrost([completion(answer(AUTH, KYC))]))
    first = p.sub_skills()[0]
    with p.w.sessions() as s:
        trail = s.scalars(
            select(AuditLog).where(AuditLog.entity_id == str(first.id)).order_by(AuditLog.id)
        ).all()
    assert [(r.after or {}).get("state") for r in trail] == ["DRAFT", "VALID"]
    assert {r.actor_id for r in trail} == {"SYSTEM"}


def test_e8_3_the_context_names_the_macros_features_and_the_registry(p: Pass2) -> None:
    bifrost = FakeBifrost([completion(answer(AUTH, KYC))])
    p.run_macro(bifrost)
    sent = "".join(str(m["content"]) for m in bifrost.requests[0]["messages"])
    assert '<data name="features">' in sent and "F-ACCESS-1" in sent and "F-ACCESS-2" in sent
    assert MACRO in sent and "psycopg" in sent


def test_e8_3_an_uncovered_feature_is_asked_again(p: Pass2) -> None:
    bifrost = FakeBifrost([completion(answer(AUTH)), completion(answer(AUTH, KYC))])
    assert len(p.run_macro(bifrost)) == 2
    assert "features.F-ACCESS-2: FEATURE_NOT_COVERED" in json.dumps(bifrost.requests[1])


def test_e8_3_a_feature_never_covered_fails_with_the_reason(p: Pass2) -> None:
    bifrost = FakeBifrost([completion(answer(AUTH))])
    err = fails(p, bifrost)
    assert err.type == FEATURE_NOT_COVERED
    assert p.workflow_row().error_reason == f"{MACRO}: feature F-ACCESS-2 not covered"
    assert len(bifrost.requests) == MAX_REASKS + 1
    assert p.sub_skills() == []


def test_e8_3_an_invented_sow_ref_is_asked_again(p: Pass2) -> None:
    invented = proposal("Investor Auth Manager", "F-ACCESS-1", "F-NOPE-9")
    bifrost = FakeBifrost([completion(answer(invented, KYC)), completion(answer(AUTH, KYC))])
    p.run_macro(bifrost)
    assert "sub_skills.0.sow_refs.1: UNKNOWN_SOW_REF F-NOPE-9" in json.dumps(bifrost.requests[1])


def test_e8_3_t07_malformed_answers_leave_the_macro_without_sub_skills(p: Pass2) -> None:
    bifrost = FakeBifrost([completion("{not json")])
    err = fails(p, bifrost)
    assert err.type == SCHEMA_INVALID
    assert p.workflow_row().error_reason == f"{MACRO}: SCHEMA_INVALID after {MAX_REASKS + 1}"
    assert len(bifrost.requests) == MAX_REASKS + 1
    assert p.sub_skills() == []


def test_e8_3_t08_rule_violations_are_flagged_and_every_field_kept(p: Pass2) -> None:
    tool = sample()["isatvon_definition"]["t_tech_stack"]["action_tool"]
    broken = proposal(
        "Investor Auth Manager",
        "F-ACCESS-1",
        **{
            IDEF + "t_tech_stack.fixed_check_tool": tool,
            IDEF + "o_outcome.state_handoff_file.mode": "overwrite",
            IDEF + "v_variables.budget_limit": "2 minutes",
        },
    )
    bifrost = FakeBifrost([completion(answer(broken, KYC))])
    p.run_macro(bifrost)
    assert len(bifrost.requests) == 1  # rule findings are never asked again
    row = p.sub_skills()[0]
    assert row.state == "INVALID"
    assert codes(row) == [
        (IDEF + "t_tech_stack.fixed_check_tool", "TOOLS_NOT_DECOUPLED"),
        (IDEF + "v_variables.budget_limit", "ISATVON_CONTRACT"),
        (IDEF + "o_outcome.state_handoff_file.mode", "ISATVON_CONTRACT"),
    ]
    definition = row.isatvon["isatvon_definition"]
    assert definition["t_tech_stack"]["fixed_check_tool"] == tool  # kept, not fixed
    assert definition["o_outcome"]["state_handoff_file"]["mode"] == "overwrite"
    assert definition["v_variables"]["budget_limit"] == "2 minutes"
    assert p.sub_skills()[1].state == "VALID"


@pytest.mark.parametrize("value", [0, 15])
def test_e8_3_max_iterations_out_of_range_is_invalid(p: Pass2, value: int) -> None:
    bad = proposal(
        "Investor Auth Manager", "F-ACCESS-1", **{IDEF + "v_variables.max_iterations": value}
    )
    p.run_macro(FakeBifrost([completion(answer(bad, KYC))]))
    row = p.sub_skills()[0]
    assert row.state == "INVALID"
    assert IDEF + "v_variables.max_iterations" in [path for path, _ in codes(row)]


def test_e8_3_an_unknown_gate_identifier_and_library_are_coded(p: Pass2) -> None:
    bad = proposal(
        "Investor Auth Manager",
        "F-ACCESS-1",
        **{
            IDEF + "v_variables.success_gate": 'auth_status == "OK" AND unknown_x > 1',
            IDEF + "t_tech_stack.libraries": [{"package": "leftpad"}],
        },
    )
    p.run_macro(FakeBifrost([completion(answer(bad, KYC))]))
    assert set(code for _, code in codes(p.sub_skills()[0])) == {
        "UNRESOLVED_IDENTIFIER",
        "LIBRARY_NOT_IN_REGISTRY",
    }


def test_e8_3_t09_a_secret_value_is_redacted_everywhere_and_flagged(
    p: Pass2, caplog: pytest.LogCaptureFixture
) -> None:
    value = "sk-or-v1-abc1234567890abcdef"
    leaky = proposal(
        "Investor Auth Manager",
        "F-ACCESS-1",
        **{IDEF + "v_variables.secrets": ["AUTH_VAULT_KEY", value]},
    )
    with caplog.at_level(logging.DEBUG):
        p.run_macro(FakeBifrost([completion(answer(leaky, KYC))]))
    row = p.sub_skills()[0]
    assert row.state == "INVALID"
    assert codes(row) == [(IDEF + "v_variables.secrets.1", "SECRET_VALUE_NOT_ALLOWED")]
    assert row.isatvon["isatvon_definition"]["v_variables"]["secrets"] == [
        "AUTH_VAULT_KEY",
        REDACTED,
    ]
    with p.w.sessions() as s:
        stored = [json.dumps(c.output) for c in s.scalars(select(LlmCall))]
    assert stored and all(value not in text for text in stored)
    assert value not in caplog.text


def test_e8_3_ids_are_sbams_and_repeated_names_are_suffixed() -> None:
    assert skill_ids("MS02_Quoting", ["Price Check", "MS02.7_Price Check", "Send / Quote!"]) == [
        "MS02.1_Price_Check",
        "MS02.2_Price_Check_2",
        "MS02.3_Send_Quote",
    ]


def test_e8_3_two_sub_skills_with_one_name_get_distinct_ids_and_names(p: Pass2) -> None:
    twice = [proposal("Investor Check", "F-ACCESS-1"), proposal("Investor Check", "F-ACCESS-2")]
    p.run_macro(FakeBifrost([completion(answer(*twice))]))
    first, second = p.sub_skills()
    assert (first.skill_id, second.skill_id) == (
        "MS01.1_Investor_Check",
        "MS01.2_Investor_Check_2",
    )
    assert first.skill_name != second.skill_name


def test_e8_3_the_last_sub_skill_points_at_the_next_macro(tmp_path: Path) -> None:
    p = Pass2(
        World(tmp_path),
        macros=[("Investor Access", ["F-ACCESS-1"]), ("Investor KYC", ["F-ACCESS-2"])],
    )
    p.run_macro(FakeBifrost([completion(answer(AUTH))]), index=0)
    (only,) = p.sub_skills(0)
    assert only.isatvon["isatvon_definition"]["n_notification"]["next"] == "MS02_Investor_KYC"
    assert only.skill_id == "MS01.1_Investor_Auth_Manager"


def test_e8_3_a_retry_returns_the_stored_sub_skills(p: Pass2) -> None:
    first = p.run_macro(FakeBifrost([completion(answer(AUTH, KYC))]))
    bifrost = FakeBifrost([completion(answer(AUTH))])
    assert p.run_macro(bifrost, attempt=2) == first
    assert bifrost.requests == [] and len(p.sub_skills()) == 2


def test_e8_3_a_sow_no_longer_approved_extracts_nothing(p: Pass2) -> None:
    p.approved(p.sow | {"overview": {**p.sow["overview"], "objective": "Changed."}})
    bifrost = FakeBifrost([completion(answer(AUTH, KYC))])
    err = fails(p, bifrost)
    assert err.type == SOW_NOT_APPROVED
    assert bifrost.requests == [] and p.sub_skills() == []


def test_e8_3_bifrost_down_retries_then_fails_llm_unavailable(p: Pass2) -> None:
    down = httpx.ConnectError("down")
    with pytest.raises(Exception) as first:
        p.run_macro(FakeBifrost([down]), attempt=1)
    assert not isinstance(first.value, ApplicationError)
    err = fails(p, FakeBifrost([down]), attempt=EXTRACT_ATTEMPTS)
    assert err.type == LLM_UNAVAILABLE
    assert p.workflow_row().error_reason == f"{MACRO}: LLM_UNAVAILABLE"


def test_e8_3_the_pipeline_plan_matches_the_attempts_used_here() -> None:
    assert PLAN["extract_sub_skills"][2] == EXTRACT_ATTEMPTS
