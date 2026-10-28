"""Apply a schema.sql to a throwaway database and prove it (E9.4, TD §12.6).

The server is `schema-check` (Compose): a PostgreSQL 16 that only the build worker can reach and
that holds nothing else, never SBAM's own database (the activity refuses a DSN on the same host).
Each check creates `sbam_check_<hex>`, applies the script in one transaction, statement by
statement (an error names its statement), then checks:

- every declared table exists (MISSING_TABLE);
- every handoff table has both guard triggers, TRUNCATE is rejected, and when a row can be inserted
  with `DEFAULT VALUES`, INSERT works and UPDATE and DELETE are rejected (NO_INSERT_ONLY_GUARD).
  ponytail: a table whose columns need values can't be probed with a row; its triggers are
  checked in the catalog instead (the guard is the platform's template, not the model's).

The database is dropped whatever happened. The server being down is `SchemaCheckUnavailable`,
retryable (T09); everything the script did wrong is a finding.
"""

import uuid
from collections.abc import Iterable, Sequence

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sbam_api.schema_sql.validate import Problem
from sbam_contracts.schema_sql import SchemaFindingCode

from sbam_workers.build.schema_sql.guard import GUARD_ERROR, ROW_TRIGGER, TRUNCATE_TRIGGER

CONNECT_TIMEOUT_SECONDS = 10
STATEMENT_TIMEOUT_MS = 30_000


class SchemaCheckUnavailable(Exception):  # noqa: N818 - retryable infrastructure failure
    pass


def _rejected(conn: psycopg.Connection, statement: sql.Composed) -> bool:
    """True when the guard raised; the statement's effects are rolled back either way."""
    try:
        with conn.transaction():
            conn.execute(statement)
            raise _Undo
    except _Undo:
        return False
    except psycopg.Error as exc:
        # the primary message, not the whole text: its CONTEXT names the guard function either way
        return (exc.diag.message_primary or "").startswith(GUARD_ERROR)


class _Undo(Exception):  # noqa: N818 - rolls a probe back
    pass


def _probe(conn: psycopg.Connection, table: str) -> str | None:
    """None when `table` is insert-only, else what got through."""
    triggers = {
        row[0]
        for row in conn.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal",
            (f"public.{table}",),
        )
    }
    if not {ROW_TRIGGER, TRUNCATE_TRIGGER} <= triggers:
        return "the insert-only guard triggers are missing"
    name = sql.Identifier(table)
    if not _rejected(conn, sql.SQL("TRUNCATE {}").format(name)):
        return "TRUNCATE was allowed"
    column = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position LIMIT 1",
        (table,),
    ).fetchone()
    try:
        with conn.transaction():
            conn.execute(sql.SQL("INSERT INTO {} DEFAULT VALUES").format(name))
            if column is not None:
                col = sql.Identifier(column[0])
                update = sql.SQL("UPDATE {} SET {} = {}").format(name, col, col)
                if not _rejected(conn, update):
                    return "UPDATE was allowed"
            if not _rejected(conn, sql.SQL("DELETE FROM {}").format(name)):
                return "DELETE was allowed"
            raise _Undo
    except _Undo:
        return None
    except psycopg.Error:
        return None  # no row without values: the catalog check above stands


def apply(
    dsn: str, statements: Sequence[str], tables: Iterable[str], handoff: Iterable[str]
) -> list[Problem]:
    """Findings (empty = PASSED). Raises `SchemaCheckUnavailable` when the server is unreachable."""
    database = f"sbam_check_{uuid.uuid4().hex}"
    try:
        admin = psycopg.connect(dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except psycopg.OperationalError as exc:
        raise SchemaCheckUnavailable(str(exc).splitlines()[0]) from exc
    with admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            return _check(make_conninfo(dsn, dbname=database), statements, tables, handoff)
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
            )


def _check(
    dsn: str, statements: Sequence[str], tables: Iterable[str], handoff: Iterable[str]
) -> list[Problem]:
    with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
        conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
        conn.commit()
        try:
            with conn.transaction():
                for index, statement in enumerate(statements):
                    try:
                        conn.execute(statement.encode())
                    except psycopg.Error as exc:
                        primary = exc.diag.message_primary or str(exc).splitlines()[0]
                        problem = Problem(SchemaFindingCode.APPLY_ERROR, primary, index)
                        raise _Failed(problem) from exc
        except _Failed as failed:
            return [failed.problem]

        present = {
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        problems = [
            Problem(SchemaFindingCode.MISSING_TABLE, f"{t} is not there after apply", table=t)
            for t in sorted(set(tables) - present)
        ]
        for table in sorted(set(handoff) & present):
            leak = _probe(conn, table)
            if leak is not None:
                problems.append(
                    Problem(
                        SchemaFindingCode.NO_INSERT_ONLY_GUARD,
                        f"handoff table {table}: {leak}",
                        table=table,
                    )
                )
        return problems


class _Failed(Exception):  # noqa: N818 - carries the first apply error out of the transaction
    def __init__(self, problem: Problem) -> None:
        super().__init__(problem.message)
        self.problem = problem
