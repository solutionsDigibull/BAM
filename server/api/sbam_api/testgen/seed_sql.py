"""The statement allowlist for a test case's seed SQL (E13.4, security).

A case's `initial_state` (fresh-state rows) and `state_script` (state changes between passes)
are SQL that the sandbox runs against a throwaway database. The model writes it and an FDE can
edit it, so the API refuses anything that is not plain data: one statement per entry,
`INSERT ... VALUES` or `UPDATE` only, on a table the Sub-Skill declares in `v_variables.tables`,
with literals (no function calls, subqueries or `RETURNING`), and at most `MAX_SEED_ROWS` rows per
case. A refused statement is never stored and never reaches the sandbox: a 422 for an FDE's edit,
a re-ask for the model.
"""

from collections.abc import Collection, Sequence

import sqlglot
from sbam_contracts.testgen import MAX_SEED_ROWS
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECT = "postgres"
# Casts are how a literal gets its type ('2026-01-01'::date); every other function is refused.
_LITERAL_FUNCTIONS = (exp.Cast, exp.TryCast)


class SeedRejected(ValueError):  # noqa: N818 - the reason is shown to the FDE
    """The statement is not allowed; the message says why (never the statement's text)."""


def _name(table: exp.Table) -> str:
    if table.args.get("db") or table.args.get("catalog"):
        raise SeedRejected("table names must not be schema-qualified")
    return str(table.name).lower()


def check_statement(sql: str, tables: Collection[str]) -> int:
    """The number of rows the statement writes; raises `SeedRejected` if it is not allowed."""
    try:
        parsed = [tree for tree in sqlglot.parse(sql, read=DIALECT) if tree is not None]
    except SqlglotError:
        raise SeedRejected("not valid PostgreSQL") from None
    if len(parsed) != 1:
        raise SeedRejected("one statement per entry")
    statement = parsed[0]
    if not isinstance(statement, exp.Insert | exp.Update):
        raise SeedRejected("only INSERT and UPDATE statements are allowed")
    target = statement.find(exp.Table)
    if target is None:
        raise SeedRejected("no table to write to")
    allowed = {t.lower() for t in tables}
    if _name(target) not in allowed:
        raise SeedRejected(f"table {_name(target)!r} is not in v_variables.tables")
    for node in statement.walk():
        if isinstance(node, exp.Select | exp.Subquery):
            raise SeedRejected("subqueries are not allowed")
        if isinstance(node, exp.Returning):
            raise SeedRejected("RETURNING is not allowed")
        if isinstance(node, exp.Func) and not isinstance(node, _LITERAL_FUNCTIONS):
            raise SeedRejected("function calls are not allowed: use literal values")
    if isinstance(statement, exp.Insert):
        values = statement.expression
        if not isinstance(values, exp.Values):
            raise SeedRejected("INSERT must use VALUES")
        return len(values.expressions)
    return 1


def check_statements(statements: Sequence[str], tables: Collection[str]) -> list[tuple[int, str]]:
    """(index, reason) of every statement that is not allowed; the empty list means all are."""
    problems: list[tuple[int, str]] = []
    for index, sql in enumerate(statements):
        try:
            check_statement(sql, tables)
        except SeedRejected as exc:
            problems.append((index, str(exc)))
    return problems


def count_rows(statements: Sequence[str], tables: Collection[str]) -> int:
    """Rows written by statements that are all allowed (call after `check_statements`)."""
    return sum(check_statement(sql, tables) for sql in statements)


def over_budget(rows: int) -> bool:
    return rows > MAX_SEED_ROWS
