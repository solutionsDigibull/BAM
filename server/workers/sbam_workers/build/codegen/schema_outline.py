"""The Workflow's tables and columns as short lines the model can follow (WG-36, #923).

The model gets schema.sql as one block, but a state query is checked against single columns: a
`CREATE TABLE` with constraints, defaults and comments is a poor list to copy names from, and the
live run of 2026-11-20 invented `attempt` columns and an `attempts_table`. The outline lists, per
table, only `column type` pairs in DDL order: nothing else is a name a query may use. It is a
second view of the same text, built here and never stored.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


def outline(schema_sql: str) -> str:
    """`table(col type, col type, ...)`, one line per table; empty when there is nothing to list."""
    try:
        statements = sqlglot.parse(schema_sql, read="postgres")
    except SqlglotError:
        return ""
    lines: list[str] = []
    for statement in statements:
        if (
            not isinstance(statement, exp.Create)
            or str(statement.args.get("kind", "")).upper() != "TABLE"
        ):
            continue
        schema = statement.this
        if not isinstance(schema, exp.Schema) or not isinstance(schema.this, exp.Table):
            continue
        columns = []
        for column in schema.expressions:
            if not isinstance(column, exp.ColumnDef):
                continue
            kind = column.args.get("kind")
            columns.append(f"{column.name} {kind.sql('postgres').lower() if kind else 'unknown'}")
        lines.append(f"{schema.this.name}({', '.join(columns)})")
    return "\n".join(sorted(lines))
