"""#746: a Sub-Skill with no table to seed (its only table is its handoff table) gets no
PASS_AFTER_RETRY, and a re-ask says why a seed statement was refused and that it must be empty."""

import hashlib
import json
from typing import Any

from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.testgen import CaseKind, TestgenOutput
from sbam_workers.test.testgen import (
    NO_TABLES_HINT,
    allowed_kinds,
    check_output,
    fingerprint,
    request_context,
)
from test_e13_4_testgen import case, sample

HANDOFF = "auth_event_audit_handoff"  # the fixture's handoff table
TABLES = "isatvon_definition.v_variables.tables"


def only_handoff() -> dict[str, Any]:
    """Like MS01.1 of RFQ Quoting Pilot: the handoff table is its only table."""
    return sample(**{TABLES: [HANDOFF]})


def output(*extra: dict[str, Any]) -> TestgenOutput:
    """The three cases a Sub-Skill without seedable tables can have, plus `extra`."""
    base = [
        case("happy", "PASS_FIRST", "SUCCESS"),
        case("never passes", "ESCALATION", "ESCALATED"),
        case("boundary", "EDGE", "SUCCESS"),
    ]
    return TestgenOutput.model_validate({"cases": [*base, *extra]})


def test_746_t01_no_seedable_table_offers_no_pass_after_retry() -> None:
    assert CaseKind.PASS_AFTER_RETRY not in allowed_kinds(only_handoff())
    limits = json.loads(request_context(only_handoff())["limits"])
    assert "PASS_AFTER_RETRY" not in limits["allowed_kinds"]
    assert CaseKind.PASS_AFTER_RETRY in allowed_kinds(sample())  # a seedable table keeps it
    retry = output(case("retry", "PASS_AFTER_RETRY", "SUCCESS"))
    assert "case 3: kind PASS_AFTER_RETRY is not allowed for this Sub-Skill" in check_output(
        only_handoff(), retry
    )


def test_746_t02_the_fingerprint_is_unchanged() -> None:
    """The bound's kinds, as before #746: no seedable table still keeps its datasets."""
    isatvon = only_handoff()
    context = request_context(isatvon)
    bound = SubSkillModel.model_validate(isatvon).isatvon_definition.v_variables.max_iterations
    kinds = [k.value for k in CaseKind if k is not CaseKind.FDE]
    before_limits = {
        "max_iterations": bound,
        "allowed_kinds": [k for k in kinds if bound > 1 or k != "PASS_AFTER_RETRY"],
    }
    before = {
        "input_schema": context["input_schema"],
        "tables": json.dumps([HANDOFF]),
        "limits": json.dumps(before_limits, sort_keys=True),
    }
    expected = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    assert fingerprint(isatvon) == expected


def test_746_t03_a_refused_seed_statement_is_re_asked_with_its_reason() -> None:
    seeded = case(
        "seeds",
        "EDGE",
        "SUCCESS",
        initial_state=["INSERT INTO other_table (id) VALUES (1)"],
        state_script=[["DELETE FROM other_table"]],
    )
    lines = check_output(only_handoff(), output(seeded))
    assert "case 3: /initial_state/0: table 'other_table' is not in v_variables.tables" in lines
    assert "case 3: /state_script/0/0: only INSERT and UPDATE statements are allowed" in lines
    assert not any("VALUES (1)" in line for line in lines)  # never the statement itself


def test_746_t04_the_no_tables_hint_comes_once_and_only_when_needed() -> None:
    seeded = case("seeds", "EDGE", "SUCCESS", initial_state=["INSERT INTO x (id) VALUES (1)"])
    lines = check_output(only_handoff(), output(seeded, {**seeded, "name": "again"}))
    assert lines.count(NO_TABLES_HINT) == 1
    assert check_output(only_handoff(), output()) == ()  # no seed SQL: nothing to say
    with_table = case("bad", "EDGE", "SUCCESS", initial_state=["INSERT INTO x (id) VALUES (1)"])
    assert NO_TABLES_HINT not in check_output(sample(), output(with_table))
