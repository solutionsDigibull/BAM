"""The model writes SQL against the Workflow's schema.sql, so it is shown it (a made-up
`fresh_state` table crashed the sandbox run; the corrector could not fix what it could not see)."""

from sbam_workers.build.codegen.bodies import _check_state_queries, request_context, schema_tables
from sbam_workers.build.codegen.params import SCHEMA_PARAM
from sbam_workers.build.codegen.render import RenderedSkeleton

DDL = """CREATE TABLE materials_issued (id bigserial PRIMARY KEY, attempt integer NOT NULL);
CREATE TABLE IF NOT EXISTS "orders" (id int);
CREATE OR REPLACE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN END; $$;"""


def skeleton() -> RenderedSkeleton:
    return RenderedSkeleton(files={}, template=None)  # type: ignore[arg-type]


def test_tables_are_read_from_the_ddl_and_nothing_else() -> None:
    assert schema_tables(DDL) == ["materials_issued", "orders"]
    assert schema_tables(None) == [] and schema_tables("") == []


def test_the_schema_is_its_own_block_and_not_in_the_manifest_block() -> None:
    ctx = request_context({"skill_id": "x", SCHEMA_PARAM: DDL}, skeleton(), [])
    assert ctx["workflow_schema_sql"] == DDL
    assert "CREATE TABLE" not in ctx["manifest"] and SCHEMA_PARAM not in ctx["manifest"]


def test_without_a_schema_the_request_is_exactly_what_it_was() -> None:
    ctx = request_context({"skill_id": "x"}, skeleton(), [])
    assert "workflow_schema_sql" not in ctx  # recorded fixtures keep their hashes


def test_a_query_on_a_table_that_does_not_exist_names_the_tables_that_do() -> None:
    found = _check_state_queries(
        {"attempt": "SELECT attempt FROM fresh_state WHERE work_order_id = %s"},
        ["attempt"],
        schema_tables(DDL),
        hint=True,
    )
    assert len(found) == 1
    assert "fresh_state" in found[0].message
    assert "tables that exist: materials_issued, orders" in found[0].message


def test_the_hint_is_only_added_when_the_schema_was_given() -> None:
    found = _check_state_queries(
        {"attempt": "SELECT attempt FROM fresh_state WHERE work_order_id = %s"}, ["attempt"], ["a"]
    )
    assert "tables that exist" not in found[0].message


def test_the_loop_gates_are_their_own_block_so_the_model_can_match_the_literal() -> None:
    from sbam_workers.build.codegen.params import GATES_PARAM
    from sbam_workers.build.wiring import gates_text

    sub = {
        "isatvon_definition": {
            "v_variables": {"success_gate": "status == 'Valid'", "max_iterations": 3},
            "o_outcome": {"json_schema": {"status": "string"}},
        }
    }
    text = gates_text(sub)
    assert "status == 'Valid'" in text
    ctx = request_context({"skill_id": "x", GATES_PARAM: text}, skeleton(), [])
    assert ctx["loop_gates"] == text and GATES_PARAM not in ctx["manifest"]
    assert "loop_gates" not in request_context({"skill_id": "x"}, skeleton(), [])
