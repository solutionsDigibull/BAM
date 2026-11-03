"""A-02 (#428): `state_queries` are parsed and restricted, not pattern-matched.

t07-t09 are the three payloads the audit's PoC got through the regex check. The AST payloads
(t01-t06) are in `test_a02_ast_allowlist.py`.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sbam_workers.build.codegen import RenderedSkeleton, state_sql
from sbam_workers.build.codegen.bodies import BodyFinding, BodyFindingCode, CodeBodies, precheck

RECORDED = Path(__file__).resolve().parents[1] / "llm-mock" / "sbam-codegen"
EMPTY = RenderedSkeleton(files={}, template=None)  # type: ignore[arg-type]


def findings(sql: str, **params: Any) -> list[BodyFinding]:
    bodies = CodeBodies(
        action_body="return None", check_body="return None", state_queries={"q": sql}, helpers=""
    )
    base = {"handoff_table": "handoff", "fresh_state": ["q"], **params}
    return precheck(bodies, ["json"], base, EMPTY)


def test_a02_t07_a_query_into_another_schema_is_rejected() -> None:
    found = findings("SELECT * FROM sbam.users WHERE id = %s")
    assert [f.code for f in found] == [BodyFindingCode.STATE_QUERY_INVALID]
    assert "schema-qualified" in found[0].message and "users" not in found[0].message


def test_a02_t08_pg_read_file_is_rejected() -> None:
    found = findings("SELECT pg_read_file('/etc/passwd') FROM handoff WHERE k = %s")
    assert [f.code for f in found] == [BodyFindingCode.STATE_QUERY_INVALID]
    assert "pg_read_file" in found[0].message and "/etc/passwd" not in found[0].message


def test_a02_t09_pg_sleep_is_rejected() -> None:
    found = findings("SELECT pg_sleep(100) FROM handoff WHERE k = %s")
    assert "pg_sleep" in found[0].message


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("WITH x AS (SELECT 1) SELECT * FROM x WHERE k = %s", "WITH"),
        ("SELECT a FROM t WHERE k = %s UNION SELECT b FROM u", "plain SELECT"),
        ("SELECT a FROM t WHERE k = %s FOR UPDATE", "locking"),
        ("SELECT a INTO newtable FROM t WHERE k = %s", "INTO"),
        ("SELECT a FROM pg_user WHERE k = %s", "system relation"),
        ("SELECT a FROM information_schema.tables WHERE k = %s", "schema-qualified"),
        ("SELECT a FROM t WHERE k = %s AND a IN (SELECT x FROM sbam.users)", "schema-qualified"),
        ("SELECT current_setting('x') FROM t WHERE k = %s", "current_setting"),
        ("SELECT a FROM t WHERE k = %s AND lo_import('/etc/passwd') > 0", "lo_import"),
        ("SELEC a FROM t WHERE k = %s", "not parseable"),
    ],
)
def test_a02_other_shapes_a_fresh_state_reader_does_not_need_are_refused(
    sql: str, reason: str
) -> None:
    reasons = state_sql.problems(sql)  # what the parser says, whatever the regex layer caught
    assert any(reason in r for r in reasons), reasons
    assert findings(sql)  # and the pre-check refuses it


def test_a02_when_the_workflows_relations_are_known_others_are_refused() -> None:
    sql = "SELECT a FROM somewhere_else WHERE k = %s"
    assert findings(sql) == []  # nothing to compare to: unqualified and non-system is accepted
    found = findings(sql, tables=["investor_auth"])
    assert "not one of the workflow's relations" in found[0].message


def test_a02_an_identifier_that_is_not_plain_is_not_echoed() -> None:
    reasons = state_sql.problems('SELECT "Weird; DROP TABLE x" FROM t WHERE k = %s', ["t"])
    assert reasons == []  # a quoted column is only a column
    reasons = state_sql.problems('SELECT a FROM "Secret Customer Table" WHERE k = %s', ["t"])
    assert reasons == ["table <name> is not one of the workflow's relations"]


def test_a02_the_recorded_legitimate_queries_still_pass() -> None:
    """MS01.1 and stock-reserve read their tables with count/sum/coalesce: all accepted."""
    seen = 0
    for name in ("ms01_1", "stock_reserve"):
        answer = json.loads((RECORDED / f"e11_2_{name}.json").read_text(encoding="utf-8"))
        for sql in answer["response"]["state_queries"].values():
            assert state_sql.problems(sql) == [], sql
            seen += 1
    assert seen >= 4
