"""#704: a case's seed SQL is checked against the column rules of the Workflow's `schema.sql`."""

import pytest
from sbam_api.testgen.seed_fit import errors, statement_errors, tables_of

SCHEMA = """
CREATE TABLE costing_module_input (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    normalized_bom_data jsonb NOT NULL
);
CREATE TABLE parts (
    part_no text,
    line serial,
    rev bigint GENERATED ALWAYS AS IDENTITY,
    note text NULL,
    qty int,
    PRIMARY KEY (part_no)
);
CREATE OR REPLACE FUNCTION sbam_handoff_insert_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'insert only';
END;
$$;
"""
TABLES = tables_of(SCHEMA)
NOT_NULL = "column normalized_bom_data is NOT NULL: give it a value, never NULL"


def test_704_columns_are_read_from_schema_sql() -> None:
    bom = TABLES["costing_module_input"]
    assert list(bom) == ["id", "created_at", "normalized_bom_data"]
    assert bom["normalized_bom_data"].not_null and not bom["normalized_bom_data"].has_default
    assert bom["id"].not_null and bom["id"].has_default  # a primary key is NOT NULL
    parts = TABLES["parts"]
    assert parts["part_no"].not_null  # table-level PRIMARY KEY
    assert parts["line"].has_default and parts["rev"].has_default  # serial, identity
    assert not parts["note"].not_null and not parts["qty"].not_null


def test_704_t01_null_into_a_not_null_column_is_refused_a_value_is_accepted() -> None:
    bad = "INSERT INTO costing_module_input (normalized_bom_data) VALUES (NULL)"
    assert statement_errors(bad, TABLES) == [NOT_NULL]
    two_rows = "INSERT INTO costing_module_input (normalized_bom_data) VALUES ('{}'), (NULL)"
    assert statement_errors(two_rows, TABLES) == [NOT_NULL]
    good = 'INSERT INTO costing_module_input (normalized_bom_data) VALUES (\'{"item": "A"}\')'
    assert statement_errors(good, TABLES) == []
    update = "UPDATE costing_module_input SET normalized_bom_data = NULL WHERE id = 'x'"
    assert statement_errors(update, TABLES) == [NOT_NULL]
    assert statement_errors("UPDATE parts SET note = NULL WHERE part_no = 'p'", TABLES) == []


def test_704_t02_a_not_null_column_without_a_default_must_be_given() -> None:
    assert statement_errors("INSERT INTO costing_module_input (id) VALUES ('x')", TABLES) == [
        "column normalized_bom_data is NOT NULL with no default: the INSERT must give it a value"
    ]
    # defaults, serial and identity columns may be left out; nullable columns too
    assert statement_errors("INSERT INTO parts (part_no) VALUES ('p-1')", TABLES) == []


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO parts (part_no, colour) VALUES ('p-1', 'red')",
        "UPDATE parts SET colour = 'red' WHERE part_no = 'p-1'",
    ],
)
def test_704_t03_an_unknown_column_is_refused(sql: str) -> None:
    assert statement_errors(sql, TABLES) == ["column colour does not exist in this table"]


def test_704_other_tables_and_unparsable_sql_are_left_to_the_allowlist() -> None:
    assert statement_errors("INSERT INTO orders (a) VALUES (NULL)", TABLES) == []
    assert statement_errors("not sql at all (", TABLES) == []
    assert statement_errors("INSERT INTO parts (part_no) VALUES (NULL)", {}) == []


def test_704_errors_give_the_case_paths() -> None:
    bad = "INSERT INTO costing_module_input (normalized_bom_data) VALUES (NULL)"
    good = "INSERT INTO parts (part_no) VALUES ('p-1')"
    assert errors([good, bad], [[good], [bad]], TABLES) == [
        ("/initial_state/1", NOT_NULL),
        ("/state_script/1/0", NOT_NULL),
    ]
