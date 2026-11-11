"""Seed statements are checked against the Workflow's schema.sql before a sandbox is spent."""

from sbam_workers.test.seed_check import columns_of, problems

DDL = """CREATE TABLE materials_issued (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  work_order_id text NOT NULL,
  issued_materials jsonb NOT NULL
);
CREATE INDEX i ON materials_issued (work_order_id);
CREATE OR REPLACE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$;"""


def test_columns_come_from_create_table_only() -> None:
    assert columns_of(DDL) == {
        "materials_issued": {"id", "created_at", "work_order_id", "issued_materials"}
    }
    assert columns_of("not sql at all (((") == {}


def test_a_seed_that_fits_has_no_problems() -> None:
    ok = "INSERT INTO materials_issued (work_order_id, issued_materials) VALUES ('WO1', '[]')"
    assert problems(DDL, [ok]) == []


def test_an_invented_column_is_named_with_the_real_ones() -> None:
    bad = (
        "INSERT INTO materials_issued (work_order_id, kit_status, status) VALUES ('WO1', 'k', 's')"
    )
    [line] = problems(DDL, [bad])
    assert "statement 1" in line and "kit_status, status" in line
    assert "columns: created_at, id, issued_materials, work_order_id" in line


def test_a_missing_table_and_a_bad_update_are_reported() -> None:
    found = problems(
        DDL,
        [
            "INSERT INTO fresh_state (a) VALUES (1)",
            "UPDATE materials_issued SET nope = 1 WHERE work_order_id = 'x'",
        ],
    )
    assert "table fresh_state does not exist" in found[0]
    assert "no column nope" in found[1]


def test_values_are_never_echoed_and_odd_identifiers_are_masked() -> None:
    [line] = problems(DDL, ["INSERT INTO materials_issued (x) VALUES ('secret-token-123')"])
    assert "secret-token-123" not in line
    [masked] = problems(DDL, ['INSERT INTO materials_issued ("Evil Col; DROP") VALUES (1)'])
    assert "DROP" not in masked and "<name>" in masked


def test_without_a_schema_there_is_nothing_to_say() -> None:
    assert problems("", ["INSERT INTO t (a) VALUES (1)"]) == []


def test_a_state_query_on_a_column_the_table_lacks_is_named() -> None:
    from sbam_workers.test.seed_check import query_problems

    [line] = query_problems(DDL, "SELECT attempt FROM materials_issued WHERE work_order_id = %s")
    assert "no column attempt" in line and "work_order_id" in line
    assert (
        query_problems(DDL, "SELECT COUNT(*) FROM materials_issued WHERE work_order_id = %s") == []
    )
    assert (
        query_problems(DDL, "SELECT COUNT(*) AS attempt FROM materials_issued WHERE id = %s") == []
    )  # an alias is not a column
    assert (
        query_problems(DDL, "SELECT x FROM a JOIN b ON a.id = b.id") == []
    )  # left to the database
    assert query_problems("", "SELECT attempt FROM materials_issued") == []
