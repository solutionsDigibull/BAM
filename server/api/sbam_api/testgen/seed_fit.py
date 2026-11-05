"""Does a case's seed SQL fit the Workflow's `schema.sql`? (#704)

`seed_sql` checks the statement shape and the table names; the sandbox then runs the statements
against a database built from `schema.sql`, and one statement that breaks a column rule refuses the
whole dataset there (`DATASET_INVALID`). This checks the column rules first, without a database:

* a NULL literal for a NOT NULL column (an INSERT value or an UPDATE `SET`);
* an INSERT that leaves out a NOT NULL column that has no default (serial and identity columns
  have one);
* a column the table does not have.

A primary key is NOT NULL. Only tables `schema.sql` creates are checked: a statement on another
table is `seed_sql`'s business. Each reason names the column and its rule, never a value.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from sbam_api.schema_sql.validate import check

DIALECT = "postgres"
_SERIAL = frozenset({"SMALLSERIAL", "SERIAL", "BIGSERIAL", "SERIAL2", "SERIAL4", "SERIAL8"})


@dataclass(frozen=True)
class Column:
    name: str
    not_null: bool
    has_default: bool


Tables = Mapping[str, Mapping[str, Column]]  # table -> column -> rule, every column in order


def _column(definition: exp.ColumnDef, key: set[str]) -> Column:
    name = definition.name.lower()
    kinds = [c.args.get("kind") for c in definition.constraints]
    not_null = name in key or any(
        isinstance(k, exp.PrimaryKeyColumnConstraint)
        or (isinstance(k, exp.NotNullColumnConstraint) and not k.args.get("allow_null"))
        for k in kinds
    )
    data_type = definition.args.get("kind")
    serial = data_type is not None and data_type.sql(dialect=DIALECT).upper() in _SERIAL
    has_default = serial or any(
        isinstance(k, exp.DefaultColumnConstraint | exp.GeneratedAsIdentityColumnConstraint)
        for k in kinds
    )
    return Column(name, not_null, has_default)


def tables_of(schema_sql: str) -> dict[str, dict[str, Column]]:
    """The columns of every table `schema_sql` creates, in their declared order."""
    found: dict[str, dict[str, Column]] = {}
    for table, statement in check(schema_sql).tables().items():
        body = statement.tree.this if statement.tree is not None else None
        parts = list(getattr(body, "expressions", []))
        key = {
            c.name.lower()
            for p in parts
            if isinstance(p, exp.PrimaryKey)
            for c in p.find_all(exp.Identifier)
        }
        found[table] = {
            d.name.lower(): _column(d, key) for d in parts if isinstance(d, exp.ColumnDef)
        }
    return found


def _null(column: Column) -> str:
    return f"column {column.name} is NOT NULL: give it a value, never NULL"


def _insert(statement: exp.Insert, columns: Mapping[str, Column]) -> list[str]:
    target = statement.this
    named = (
        [c.name.lower() for c in target.expressions]
        if isinstance(target, exp.Schema)
        else list(columns)
    )
    reasons = [f"column {n} does not exist in this table" for n in named if n not in columns]
    left_out = [c for c in columns.values() if c.not_null and not c.has_default]
    reasons += [
        f"column {c.name} is NOT NULL with no default: the INSERT must give it a value"
        for c in left_out
        if c.name not in named
    ]
    values = statement.expression
    rows = values.expressions if isinstance(values, exp.Values) else []
    nulls: set[str] = set()
    for row in rows:
        for name, value in zip(named, getattr(row, "expressions", []), strict=False):
            if isinstance(value, exp.Null) and name in columns and columns[name].not_null:
                nulls.add(name)
    reasons += [_null(columns[n]) for n in sorted(nulls)]
    return reasons


def _update(statement: exp.Update, columns: Mapping[str, Column]) -> list[str]:
    reasons: list[str] = []
    for assignment in statement.expressions:
        if not isinstance(assignment, exp.EQ):
            continue
        name = assignment.this.name.lower()
        if name not in columns:
            reasons.append(f"column {name} does not exist in this table")
        elif isinstance(assignment.expression, exp.Null) and columns[name].not_null:
            reasons.append(_null(columns[name]))
    return reasons


def statement_errors(sql: str, tables: Tables) -> list[str]:
    """Why the statement breaks a column rule of `tables`; empty when it fits (or when it is not
    an INSERT / UPDATE on one of them, which `seed_sql` judges)."""
    try:
        statement = sqlglot.parse_one(sql, read=DIALECT)
    except SqlglotError:
        return []
    target = statement.find(exp.Table)
    if target is None:
        return []
    columns = tables.get(str(target.name).lower())
    if columns is None:
        return []
    if isinstance(statement, exp.Insert):
        return _insert(statement, columns)
    if isinstance(statement, exp.Update):
        return _update(statement, columns)
    return []


def errors(
    initial_state: Sequence[str], state_script: Sequence[Sequence[str]], tables: Tables
) -> list[tuple[str, str]]:
    """(path, reason) for every column rule a case's seed SQL breaks, paths as `case_errors`."""
    found = [
        (f"/initial_state/{i}", reason)
        for i, sql in enumerate(initial_state)
        for reason in statement_errors(sql, tables)
    ]
    found += [
        (f"/state_script/{step}/{i}", reason)
        for step, statements in enumerate(state_script)
        for i, sql in enumerate(statements)
        for reason in statement_errors(sql, tables)
    ]
    return found
