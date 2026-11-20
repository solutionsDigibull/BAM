"""E15.3 (#137) SQL in identifiers: a table name never carries SQL into schema.sql.

Every payload of the versioned corpus `fixtures/security/sql_identifier_injection.txt` is put where
an ISATVON 1.4 Sub-Skill names a table (`v_variables.tables`, `o_outcome.state_handoff_file.table`).
Then:

* the ISATVON rules (`validate_sub_skill`, rules 6 and 7) refuse it, so the Sub-Skill never
  reaches the build. A PostgreSQL reserved word (`user`, `select`) has the shape of a table name
  and passes them; the schema step below is what refuses it;
* the schema step refuses it too (`merge`: INVALID_TABLE_NAME), and the merged schema.sql and the
  insert-only guard (`guard_sql`, which writes names into SQL text) never contain it.

The payloads are refused by name, never escaped: only `TABLE_NAME` names reach SQL text.
"""

import copy
import json
import re
from typing import Any

import pytest
from fixtures import fixture_path
from sbam_contracts.isatvon14 import validate_sub_skill
from sbam_contracts.schema_sql import TABLE_NAME
from sbam_workers.build.schema_sql.merge import declared, merge, name_problem

CORPUS = fixture_path("security", "sql_identifier_injection.txt")
ISA14 = fixture_path("isa14", "ms01_1_structured.json")


def load_corpus() -> list[tuple[int, str]]:
    return [
        (number, line)
        for number, line in enumerate(CORPUS.read_text(encoding="utf-8").splitlines(), start=1)
        if line.strip() and not line.startswith("# ")
    ]


PAYLOADS = load_corpus()
IDS = [f"line{number}" for number, _ in PAYLOADS]


def test_e15_3_the_sql_identifier_corpus_has_at_least_50_payloads() -> None:
    assert len(PAYLOADS) >= 50
    assert len({p for _, p in PAYLOADS}) == len(PAYLOADS), "duplicate payloads"


def _reference() -> dict[str, Any]:
    raw = json.loads(ISA14.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert validate_sub_skill(raw) == [], "the reference Sub-Skill must be valid to start with"
    return raw


REFERENCE = _reference()


@pytest.mark.parametrize(("number", "payload"), PAYLOADS, ids=IDS)
def test_e15_3_isatvon_rules_refuse_a_payload_as_a_table(number: int, payload: str) -> None:
    if re.match(TABLE_NAME, payload):
        pytest.skip("a reserved word: refused by the schema step (next test), not by the shape")
    raw = copy.deepcopy(REFERENCE)
    definition = raw["isatvon_definition"]
    definition["v_variables"]["tables"] = [payload]
    definition["o_outcome"]["state_handoff_file"]["table"] = payload
    paths = {v.path for v in validate_sub_skill(raw)}
    assert "isatvon_definition.v_variables.tables.0" in paths, (number, payload)
    assert "isatvon_definition.o_outcome.state_handoff_file.table" in paths, (number, payload)


@pytest.mark.parametrize(("number", "payload"), PAYLOADS, ids=IDS)
def test_e15_3_the_schema_step_refuses_a_payload_and_writes_no_sql_with_it(
    number: int, payload: str
) -> None:
    assert name_problem(payload) is not None, (number, payload)
    sub = {
        "skill_metadata": {"skill_id": "MS01.1_Login"},
        "isatvon_definition": {
            "v_variables": {"tables": ["investors", payload]},
            "o_outcome": {"state_handoff_file": {"table": payload, "required_fields": ["id"]}},
        },
    }
    merged = merge(declared([sub]), None, {})
    assert [(p.code.value, p.table) for p in merged.problems if p.table == payload] == [
        ("INVALID_TABLE_NAME", payload)
    ], (number, payload)
    assert payload not in merged.tables
    assert payload not in merged.sql, (number, payload)
