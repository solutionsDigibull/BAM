"""#738: the handoff table (always in v_variables.tables) is never offered as a table to seed."""

import hashlib
import json
from typing import Any

from sbam_api.testgen.validate import case_errors, declared_tables
from sbam_contracts.testgen import CaseBody, CaseKind, ExpectedTerminal
from sbam_workers.test.testgen import fingerprint, request_context
from test_e13_4_testgen import INPUT, sample

HANDOFF = "auth_event_audit_handoff"  # the fixture's handoff table
TABLES = "isatvon_definition.v_variables.tables"


def only_handoff() -> dict[str, Any]:
    """MS01.1 of RFQ Quoting Pilot: its one table is its handoff table, listed twice."""
    return sample(**{TABLES: [HANDOFF, HANDOFF]})


def seeding(table: str) -> CaseBody:
    given: dict[str, Any] = dict(INPUT)
    return CaseBody(
        name="seeds",
        input=given,
        initial_state=[f"INSERT INTO {table} (id) VALUES (1)"],  # noqa: S608 - a test literal
        expected_terminal=ExpectedTerminal.SUCCESS,
    )


def test_738_t01_the_handoff_table_and_duplicates_are_left_out() -> None:
    assert declared_tables(only_handoff()) == []
    assert declared_tables(sample()) == ["investor_profiles"]
    upper = sample(**{TABLES: ["investor_profiles", HANDOFF.upper(), "investor_profiles"]})
    assert declared_tables(upper) == ["investor_profiles"]


def test_738_t02_the_request_offers_only_seedable_tables() -> None:
    assert json.loads(request_context(only_handoff())["tables"]) == []
    assert json.loads(request_context(sample())["tables"]) == ["investor_profiles"]


def test_738_t03_a_case_that_seeds_the_handoff_table_is_refused() -> None:
    refused = case_errors(sample(), CaseKind.PASS_FIRST, seeding(HANDOFF))
    assert [e.path for e in refused] == ["/initial_state/0"]
    assert case_errors(sample(), CaseKind.PASS_FIRST, seeding("investor_profiles")) == []


def test_738_the_fingerprint_is_unchanged() -> None:
    """Every Sub-Skill lists its handoff table (ISATVON rule 7): a changed fingerprint would make
    every FDE-edited dataset stale and regenerate every other one."""
    raw = sample()["isatvon_definition"]["v_variables"]["tables"]
    context = request_context(sample())
    before = {
        "input_schema": context["input_schema"],
        "tables": json.dumps(raw),
        "limits": context["limits"],
    }
    expected = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    assert fingerprint(sample()) == expected
