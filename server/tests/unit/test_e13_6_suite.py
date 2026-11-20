"""E13.6: the suite builder, from the MS01.1 fixture and a few cases."""

import copy
import json
from typing import Any

import pytest
from fixtures import fixture_path
from sbam_contracts.promptfoo import AssertionType, suite_sha256
from sbam_workers.test.suite import (
    SuiteBuildError,
    bounds_of,
    build_suite,
    free_text_only,
    output_schema_of,
)

INPUT = {"mobile_number": "9999900000", "otp_payload": "123456", "device_fingerprint": "fp-1"}


def isatvon() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8")
    )
    return copy.deepcopy(raw)


def case(name: str, kind: str, terminal: str, **over: Any) -> dict[str, Any]:
    return {"name": name, "kind": kind, "input": INPUT, "expected_terminal": terminal, **over}


CASES = [
    case("happy", "PASS_FIRST", "SUCCESS"),
    case("never passes", "ESCALATION", "ESCALATED"),
    case("nothing to do", "NO_OP", "NO_OP"),
]


def types(suite: Any, name: str) -> list[AssertionType]:
    (found,) = [c for c in suite.cases if c.name == name]
    return [a.type for a in found.assertions]


def test_e13_6_the_bounds_come_from_the_v_variables_and_the_handoff_table() -> None:
    bounds = bounds_of(isatvon())
    assert bounds.max_iterations >= 1 and bounds.budget_ms >= 1
    assert bounds.handoff_fields  # the handoff table's required fields


def test_e13_6_the_output_schema_requires_the_handoff_id_and_every_declared_output() -> None:
    schema = output_schema_of(isatvon())
    assert "state_handoff_id" in schema["required"]
    assert set(schema["required"]) == set(schema["properties"])


def test_e13_6_a_schema_without_the_handoff_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import sbam_workers.test.suite as module

    monkeypatch.setattr(module, "build_contracts", lambda _: {"output_schema": {"properties": {}}})
    with pytest.raises(SuiteBuildError, match="state_handoff_id"):
        output_schema_of(isatvon())


def test_e13_6_every_case_starts_with_the_deterministic_assertions() -> None:
    suite = build_suite(isatvon(), CASES)
    for name in ("happy", "never passes", "nothing to do"):
        got = types(suite, name)
        assert got[0] is AssertionType.TERMINAL_STATE
        assert AssertionType.HANDOFF_ROWS in got and AssertionType.UI_UPDATES in got
        assert AssertionType.LLM_RUBRIC not in got


def test_e13_6_only_a_success_case_is_checked_for_outputs() -> None:
    suite = build_suite(isatvon(), CASES)
    assert AssertionType.STATE_HANDOFF_ID in types(suite, "happy")
    assert AssertionType.OUTPUT_SCHEMA in types(suite, "happy")
    assert AssertionType.STATE_HANDOFF_ID not in types(suite, "never passes")


def test_e13_6_declared_expected_output_and_known_extra_assertions_are_kept() -> None:
    extra = [{"type": "iterations_within_bound"}, {"type": "ui_updates", "value": ["x"]}]
    cases = [
        case("happy", "PASS_FIRST", "SUCCESS", expected_output={"status": "ok"}, assertions=extra),
        *CASES[1:],
    ]
    got = types(build_suite(isatvon(), cases), "happy")
    assert AssertionType.EXPECTED_OUTPUT in got
    assert got.count(AssertionType.ITERATIONS_WITHIN_BOUND) == 1  # not repeated


def test_e13_6_an_unknown_assertion_type_is_refused_not_ignored() -> None:
    bad = [case("happy", "PASS_FIRST", "SUCCESS", assertions=[{"type": "looks_fine"}]), *CASES[1:]]
    with pytest.raises(SuiteBuildError, match="looks_fine"):
        build_suite(isatvon(), bad)


def test_e13_6_t09_the_same_inputs_give_the_same_hash_in_any_case_order() -> None:
    first = suite_sha256(build_suite(isatvon(), CASES))
    again = suite_sha256(build_suite(isatvon(), list(reversed(CASES))))
    assert first == again


def test_e13_6_a_different_dataset_gives_a_different_hash() -> None:
    changed = [
        case("happy", "PASS_FIRST", "SUCCESS", input={**INPUT, "otp_payload": "0"}),
        *CASES[1:],
    ]
    assert suite_sha256(build_suite(isatvon(), changed)) != suite_sha256(
        build_suite(isatvon(), CASES)
    )


def test_e13_6_free_text_only_outputs_get_one_rubric_last_and_only_on_success() -> None:
    sub = isatvon()
    sub["isatvon_definition"]["o_outcome"]["json_schema"] = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
    }
    assert free_text_only(output_schema_of(sub))
    suite = build_suite(sub, CASES, rubric="the summary is polite")
    happy = types(suite, "happy")
    assert happy[-1] is AssertionType.LLM_RUBRIC and happy.count(AssertionType.LLM_RUBRIC) == 1
    assert AssertionType.OUTPUT_SCHEMA in happy  # the schema check comes with the rubric
    assert AssertionType.LLM_RUBRIC not in types(suite, "never passes")


def test_e13_6_an_enum_or_format_field_is_not_free_text() -> None:
    sub = isatvon()
    sub["isatvon_definition"]["o_outcome"]["json_schema"] = {
        "type": "object",
        "properties": {"summary": {"type": "string"}, "tier": {"type": "string", "enum": ["a"]}},
    }
    assert not free_text_only(output_schema_of(sub))
    assert AssertionType.LLM_RUBRIC not in types(build_suite(sub, CASES, rubric="r"), "happy")


def stored_rubric(text: Any = "the summary is polite") -> list[dict[str, Any]]:
    return [{"type": "llm_rubric", "value": text}]


def test_e13_6_t_rubric_1_a_stored_rubric_reaches_the_suite_as_the_last_assertion() -> None:
    cases = [
        case("happy", "PASS_FIRST", "SUCCESS", assertions=stored_rubric()),
        case("never passes", "ESCALATION", "ESCALATED"),
        case("nothing to do", "NO_OP", "NO_OP"),
    ]
    suite = build_suite(isatvon(), cases)
    happy = types(suite, "happy")
    assert happy[-1] is AssertionType.LLM_RUBRIC and happy.count(AssertionType.LLM_RUBRIC) == 1
    assert happy[0] is AssertionType.TERMINAL_STATE  # a rubric is never the only check
    (found,) = [c for c in suite.cases if c.name == "happy"]
    assert found.assertions[-1].value == "the summary is polite"
    assert AssertionType.LLM_RUBRIC not in types(suite, "never passes")


def test_e13_6_t_rubric_2_a_stored_rubric_goes_last_even_when_listed_first() -> None:
    mixed = [
        *stored_rubric(),
        {"type": "ui_updates"},
        {"type": "expected_output", "value": {"state_handoff_id": "x"}},
    ]
    suite = build_suite(
        isatvon(), [case("happy", "PASS_FIRST", "SUCCESS", assertions=mixed), *CASES[1:]]
    )
    got = types(suite, "happy")
    assert got.count(AssertionType.LLM_RUBRIC) == 1 and got[-1] is AssertionType.LLM_RUBRIC


@pytest.mark.parametrize("value", [None, "", "   ", 7, {"a": 1}])
def test_e13_6_t_rubric_3_a_stored_rubric_without_text_is_a_suite_build_error(value: Any) -> None:
    bad = [case("happy", "PASS_FIRST", "SUCCESS", assertions=stored_rubric(value)), *CASES[1:]]
    with pytest.raises(SuiteBuildError, match="needs its text"):
        build_suite(isatvon(), bad)


def test_e13_6_t_rubric_4_a_stored_rubric_wins_over_the_given_one_and_changes_the_hash() -> None:
    plain = build_suite(isatvon(), CASES)
    cases = [
        case("happy", "PASS_FIRST", "SUCCESS", assertions=stored_rubric("mine")),
        *CASES[1:],
    ]
    with_rubric = build_suite(isatvon(), cases, rubric="theirs")
    (found,) = [c for c in with_rubric.cases if c.name == "happy"]
    assert [a.value for a in found.assertions if a.type is AssertionType.LLM_RUBRIC] == ["mine"]
    assert suite_sha256(with_rubric) != suite_sha256(plain)


def test_e13_6_t_rubric_5_the_rubric_reaches_the_grader_request() -> None:
    import asyncio

    from sbam_workers.test.grader import request_for
    from sbam_workers.test.judge import grade_with_retries, needs_rubric
    from test_e13_6_judge import Grader, outputs

    cases = [
        case("pass_first", "PASS_FIRST", "SUCCESS", assertions=stored_rubric("reads well")),
        case("escalation", "ESCALATION", "ESCALATED"),
        case("no_op", "NO_OP", "NO_OP"),
    ]
    suite = build_suite(isatvon(), cases)
    asked = needs_rubric(suite)
    assert [c.name for c in asked] == ["pass_first"]
    [sent] = request_for(asked, outputs())["cases"]
    assert sent["name"] == "pass_first" and sent["rubric"] == "reads well"
    grader = Grader()
    assert asyncio.run(grade_with_retries(grader, asked, outputs())) == {"pass_first": True}
    assert grader.calls == 1
