"""E13.4 / #622: the seed-SQL allowlist accepts plain data statements and refuses real functions.

The check once refused every `WHERE a = 'x' AND b = 'y'` as a function call, because sqlglot makes
`And` and `Or` subclasses of `Func`. The model writes that form all the time to address one row.
"""

import pytest
from sbam_api.testgen.seed_sql import SeedRejected, check_statement, check_statements

TABLES = ["stock_levels", "orders"]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE stock_levels SET stock_on_hand = 5 WHERE sku = 'P-1' AND warehouse_code = 'WH1'",
        "UPDATE stock_levels SET stock_on_hand = 5 WHERE sku = 'P-1' OR sku = 'P-2'",
        "UPDATE stock_levels SET stock_on_hand = 0 WHERE a = 1 AND b = 2 AND c = 3",
        "UPDATE stock_levels SET stock_on_hand = 0 WHERE (a = 1 OR a = 2) AND b = 3",
        "UPDATE stock_levels SET stock_on_hand = 0 WHERE NOT a = 1 AND b = 2",
        "UPDATE stock_levels SET stock_on_hand = stock_on_hand - 1 WHERE sku = 'P-1'",
        "UPDATE orders SET placed = '2026-01-01'::date WHERE id = 1 AND status = 'NEW'",
        "INSERT INTO orders (id, status, placed) VALUES (1, 'NEW', '2026-01-01'::date)",
        "UPDATE orders SET status = 'PAID' WHERE id = 1",
    ],
)
def test_e13_4_a_plain_statement_is_accepted_whatever_its_where_joins(statement: str) -> None:
    assert check_statement(statement, TABLES) >= 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE orders SET placed = NOW() WHERE id = 1",
        "UPDATE orders SET placed = CURRENT_DATE WHERE id = 1 AND status = 'NEW'",
        "UPDATE orders SET status = UPPER('x') WHERE id = 1",
        "UPDATE orders SET status = COALESCE('a', 'b') WHERE id = 1",
        "INSERT INTO orders (id) VALUES (gen_random_uuid())",
        "UPDATE orders SET status = 'x' WHERE id = 1 AND placed < NOW()",  # inside an AND
        "UPDATE orders SET status = 'x' WHERE id = 1 OR lower(status) = 'new'",  # inside an OR
    ],
)
def test_e13_4_a_real_function_is_still_refused_even_inside_an_and_or_or(statement: str) -> None:
    with pytest.raises(SeedRejected, match="function"):
        check_statement(statement, TABLES)


@pytest.mark.parametrize(
    ("statement", "reason"),
    [
        ("UPDATE orders SET status = 'x' WHERE id IN (SELECT id FROM orders)", "subquer"),
        ("UPDATE orders SET status = 'x' WHERE id = 1 AND id IN (SELECT 1)", "subquer"),
        ("UPDATE orders SET status = 'x' WHERE id = 1 RETURNING id", "RETURNING"),
        ("DELETE FROM orders WHERE id = 1 AND status = 'x'", "INSERT and UPDATE"),
        ("UPDATE elsewhere SET a = 1 WHERE b = 1 AND c = 2", "not in v_variables.tables"),
        ("UPDATE public.orders SET a = 1 WHERE b = 1 AND c = 2", "schema-qualified"),
    ],
)
def test_e13_4_the_other_refusals_still_hold_with_an_and(statement: str, reason: str) -> None:
    with pytest.raises(SeedRejected, match=reason):
        check_statement(statement, TABLES)


def test_e13_4_the_row_count_of_a_statement_with_an_and_is_unchanged() -> None:
    assert (
        check_statement("UPDATE orders SET status = 'x' WHERE id = 1 AND status = 'y'", TABLES) == 1
    )
    insert = "INSERT INTO orders (id, status) VALUES (1, 'a'), (2, 'b'), (3, 'c')"
    assert check_statement(insert, TABLES) == 3


def test_e13_4_check_statements_reports_only_the_statement_that_is_refused() -> None:
    problems = check_statements(
        [
            "UPDATE stock_levels SET stock_on_hand = 5 WHERE sku = 'P-1' AND warehouse = 'W1'",
            "UPDATE orders SET placed = NOW() WHERE id = 1",
        ],
        TABLES,
    )
    assert [index for index, _ in problems] == [1]
