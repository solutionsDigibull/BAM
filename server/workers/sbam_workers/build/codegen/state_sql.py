"""Fresh-state SQL from the model, parsed and restricted (audit A-02, #428).

`state_queries` came in as text and was checked with a regex (`startswith("SELECT")`, one `%s`, no
`;`). That lets `SELECT * FROM sbam.users`, `pg_read_file(...)` and `pg_sleep(...)` through. This
module parses the query with sqlglot (PostgreSQL dialect) and accepts only what a fresh-state reader
needs:

  * one statement, a plain `SELECT`: no CTE, no set operation, no `INTO`, no locking clause;
  * tables unqualified (no schema or catalog), never a system relation (`pg_*`,
    `information_schema`), and, when the caller knows the workflow's relations, one of them;
  * functions from `ALLOWED_FUNCTIONS` only; every other call, including every `pg_*` function, is
    refused, and so is anything sqlglot cannot parse.

`problems` returns short reasons that name constructs, never the query text: an identifier the
model chose is echoed only when it is a plain lower-case identifier. It is a fast fail; the
authority is the database role the sandbox runs the query under (read-only, scoped to the workflow's
schema, with a statement timeout; docs/adr/0026).
"""

import re
from collections.abc import Iterable
from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_FUNCTIONS: Final = frozenset(
    {
        "Count", "Sum", "Min", "Max", "Avg", "Coalesce", "Nullif", "Greatest", "Least", "Abs",
        "Round", "Lower", "Upper", "Length", "Trim", "Cast", "Case", "If",
        # boolean and comparison connectors sqlglot models as functions
        "And", "Or", "Not", "Between",
    }
)  # fmt: skip
_SYSTEM_PREFIXES: Final = ("pg_", "information_schema")
_PLAIN: Final = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _named(identifier: str) -> str:
    return repr(identifier) if _PLAIN.match(identifier) else "<name>"


def problems(sql: str, tables: Iterable[str] | None = None) -> list[str]:
    """Why this fresh-state query is refused (empty = accepted)."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except SqlglotError:
        return ["not parseable as a PostgreSQL query"]
    roots = [s for s in statements if s is not None]
    if len(roots) != 1:
        return ["must be exactly one statement"]
    root = roots[0]
    if not isinstance(root, exp.Select):
        return [f"must be a plain SELECT, not {type(root).__name__}"]
    out: list[str] = []
    for kind, label in (
        (exp.With, "a WITH clause"),
        (exp.Union, "a set operation"),
        (exp.Into, "SELECT INTO"),
        (exp.Lock, "a locking clause"),
    ):
        if root.find(kind) is not None:
            out.append(f"{label} is not allowed")
    allowed = None if tables is None else {t.lower() for t in tables}
    for table in root.find_all(exp.Table):
        name = table.name
        if table.db or table.catalog:
            out.append("a schema-qualified table is not allowed")
        elif not name or name.lower().startswith(_SYSTEM_PREFIXES):
            out.append(f"system relation {_named(name)} is not allowed")
        elif allowed is not None and name.lower() not in allowed:
            out.append(f"table {_named(name)} is not one of the workflow's relations")
    for func in root.find_all(exp.Func):
        if isinstance(func, exp.Anonymous):
            out.append(f"function {_named(func.name)} is not allowed")
        elif type(func).__name__ not in ALLOWED_FUNCTIONS:
            out.append(f"function {type(func).__name__} is not allowed")
    return list(dict.fromkeys(out))
