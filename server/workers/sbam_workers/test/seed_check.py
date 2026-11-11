"""Do a test case's seed statements fit the Workflow's schema.sql? Checked before a sandbox runs.

The model writes `INSERT INTO t (a, b) VALUES (...)` for the cases and was never told the
columns, so it invented some (`kit_status`, `status`), and every run then ended DATASET_INVALID
in the VM after booting it. This reads the tables and columns from the DDL (sqlglot, PostgreSQL)
and names, per statement, an unknown table or column. Messages name identifiers only.
"""

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def columns_of(schema_sql: str) -> dict[str, set[str]]:
    """table -> its columns, from the CREATE TABLE statements (anything else is ignored)."""
    tables: dict[str, set[str]] = {}
    try:
        statements = sqlglot.parse(schema_sql, read="postgres")
    except SqlglotError:
        return tables
    for statement in statements:
        if (
            not isinstance(statement, exp.Create)
            or statement.args.get("kind", "").upper() != "TABLE"
        ):
            continue
        schema = statement.this
        if not isinstance(schema, exp.Schema) or not isinstance(schema.this, exp.Table):
            continue
        cols = {c.name.lower() for c in schema.expressions if isinstance(c, exp.ColumnDef)}
        tables[schema.this.name.lower()] = cols
    return tables


def _shown(name: str) -> str:
    return name if _IDENT.match(name) else "<name>"


def problems(schema_sql: str, statements: list[str]) -> list[str]:
    """One short line per statement that names a table or column the schema does not have."""
    tables = columns_of(schema_sql)
    if not tables:
        return []  # no schema to compare with: nothing to say
    found: list[str] = []
    for n, text in enumerate(statements, 1):
        try:
            tree = sqlglot.parse_one(text, read="postgres")
        except SqlglotError:
            continue  # the case validator already refuses what it cannot parse
        table: str | None = None
        used: list[str] = []
        if isinstance(tree, exp.Insert) and isinstance(tree.this, exp.Schema):
            target = tree.this.this
            table = target.name.lower() if isinstance(target, exp.Table) else None
            used = [c.name.lower() for c in tree.this.expressions]
        elif isinstance(tree, exp.Insert) and isinstance(tree.this, exp.Table):
            table = tree.this.name.lower()
        elif isinstance(tree, exp.Update) and isinstance(tree.this, exp.Table):
            table = tree.this.name.lower()
            used = [
                e.this.name.lower()
                for e in tree.expressions
                if isinstance(e, exp.EQ) and isinstance(e.this, exp.Column)
            ]
        if table is None:
            continue
        if table not in tables:
            found.append(f"statement {n}: table {_shown(table)} does not exist")
            continue
        unknown = sorted({c for c in used if c not in tables[table]})
        if unknown:
            names = ", ".join(_shown(c) for c in unknown)
            have = ", ".join(sorted(tables[table]))
            found.append(f"statement {n}: {_shown(table)} has no column {names} (columns: {have})")
    return found


def query_problems(schema_sql: str, sql: str) -> list[str]:
    """Columns a single-table SELECT names that the table does not have (a query with joins or
    subqueries is left to the database: only the unambiguous case is checked)."""
    tables = columns_of(schema_sql)
    if not tables:
        return []
    try:
        tree = sqlglot.parse_one(sql.replace("%s", "NULL"), read="postgres")
    except SqlglotError:
        return []
    if not isinstance(tree, exp.Select):
        return []
    sources = list(tree.find_all(exp.Table))
    if len(sources) != 1 or tree.find(exp.Join) or tree.find(exp.Subquery):
        return []
    table = sources[0].name.lower()
    if table not in tables:
        return []  # a missing table is reported by the table check
    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    unknown = sorted({c.name.lower() for c in tree.find_all(exp.Column)} - tables[table] - aliases)
    if not unknown:
        return []
    names = ", ".join(_shown(c) for c in unknown)
    have = ", ".join(sorted(tables[table]))
    return [f"{_shown(table)} has no column {names} (columns: {have})"]
