"""E6.2c: a SOW that fails a document-level rule gets feedback the model can act on (path + code +
safe id), never `<root>: value_error`, and never document text (E1.21)."""

import asyncio
import copy
import json
from typing import Any

import pytest
from fixtures import fixture_path
from pydantic import ValidationError
from sbam_contracts.sow import SowDocument
from sbam_workers.llm.reask import (
    Message,
    Reply,
    SchemaInvalidAfterRetries,
    error_lines,
    run_structured,
    schema_names,
)

NAMES = schema_names(SowDocument)
SMOKE: list[Message] = [{"role": "user", "content": "sow"}]


def good() -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(
        fixture_path("sow", "sow_rfq_nav9.json").read_text(encoding="utf-8")
    )
    return raw


def lines(doc: dict[str, Any]) -> tuple[str, ...]:
    with pytest.raises(ValidationError) as caught:
        SowDocument.model_validate_json(json.dumps(doc), strict=True)
    return error_lines(caught.value, NAMES)


def test_e6_2c_t01_none_identified_in_rule_refs_is_named_by_position_and_code() -> None:
    doc = good()
    doc["rules_constraints"]["rule_refs"] = ["None identified"]
    assert lines(doc) == ("rules_constraints.rule_refs[0]: NOT_A_RULE_ID",)


def test_e6_2c_t01_the_second_answer_fixes_it_after_the_coded_feedback() -> None:
    bad = good()
    bad["rules_constraints"]["rule_refs"] = ["None identified"]
    fixed = good()
    fixed["rules_constraints"]["rule_refs"] = []
    answers = [json.dumps(bad), json.dumps(fixed)]
    seen: list[list[Message]] = []

    async def call(messages: list[Message]) -> Reply:
        seen.append(messages)
        return Reply(answers[len(seen) - 1], "m")

    result = asyncio.run(run_structured(call, SMOKE, SowDocument))
    assert [a.status for a in result.attempts] == ["SCHEMA_INVALID", "SUCCEEDED"]
    feedback = seen[1][-1]["content"]
    assert "rules_constraints.rule_refs[0]: NOT_A_RULE_ID" in feedback
    assert "None identified" not in feedback and "<root>" not in feedback


def test_e6_2c_t02_a_module_without_a_feature_is_named_with_its_id() -> None:
    doc = good()
    doc["modules"].append({**copy.deepcopy(doc["modules"][0]), "id": "M-X"})
    assert lines(doc) == ("modules[2]: MODULE_WITHOUT_FEATURE M-X",)


def test_e6_2c_t03_a_feature_naming_an_unknown_module() -> None:
    doc = good()
    doc["features"][1]["module_id"] = "M-NOPE"
    assert lines(doc) == ("features[1].module_id: UNKNOWN_MODULE M-NOPE",)


def test_e6_2c_t04_a_duplicate_feature_or_module_id() -> None:
    doc = good()
    doc["features"][1]["id"] = doc["features"][0]["id"]
    assert lines(doc) == ("features[0]: DUPLICATE_ID F-INTAKE-1",)
    modules = good()
    modules["modules"][1]["id"] = modules["modules"][0]["id"]
    assert lines(modules)[0].startswith("modules[0]: DUPLICATE_ID M-INTAKE")


def test_e6_2c_t05_the_same_tool_for_action_and_check() -> None:
    doc = good()
    doc["features"][0]["tools"] = {"action": ["parse_bom"], "check": ["Parse_BOM"]}
    assert lines(doc) == ("features[0].tools: ACTION_CHECK_OVERLAP",)


def test_e6_2c_t06_feedback_never_carries_document_text() -> None:
    doc = good()
    doc["rules_constraints"]["rule_refs"] = ["TOP-SECRET customer text"]
    doc["features"][0]["tools"] = {"action": ["secret_tool"], "check": ["SECRET_TOOL"]}
    every = " ".join(lines(doc))
    assert "TOP-SECRET" not in every and "secret_tool" not in every.lower()


def test_e6_2c_a_sow_that_stays_wrong_still_ends_as_schema_invalid() -> None:
    bad = good()
    bad["rules_constraints"]["rule_refs"] = ["None identified"]

    async def call(_: list[Message]) -> Reply:
        return Reply(json.dumps(bad), "m")

    with pytest.raises(SchemaInvalidAfterRetries) as caught:
        asyncio.run(run_structured(call, SMOKE, SowDocument))
    assert caught.value.attempts[-1].errors == ("rules_constraints.rule_refs[0]: NOT_A_RULE_ID",)
