"""The Workflow's schema.sql from its declared tables, the uploaded base and the generated
fragments (E9.4, TD §12.6). Pure and deterministic (T08): the same inputs in any Sub-Skill order
give byte-identical SQL.

Order of the script:
1. the uploaded base, verbatim, in its own order (its foreign keys may need it);
2. generated `CREATE EXTENSION`s, then tables (a table after the tables it references, otherwise
   by name), then functions, indexes and triggers, each sorted and de-duplicated;
3. the platform guard of every handoff table (`guard.py`), last.

A table defined twice the same way (columns compared as a set) is kept once; defined differently,
it is a CONFLICT finding, never last-write-wins (R17). A declared table without DDL is
MISSING_TABLE; a handoff table without one of its `required_fields` is MISSING_TABLE too.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sbam_api.schema_sql.validate import Problem, Script, Statement
from sbam_contracts.schema_sql import TABLE_NAME, SchemaFindingCode
from sqlglot import exp

from sbam_workers.build.schema_sql.guard import guard_sql

# PostgreSQL's reserved key words (Appendix C, "reserved" in the PostgreSQL column): a table
# named after one only works quoted, and generated code never quotes, so it is refused.
RESERVED = frozenset(
    """all analyse analyze and any array as asc asymmetric authorization binary both case cast
    check collate collation column concurrently constraint create cross current_catalog
    current_date current_role current_schema current_time current_timestamp current_user default
    deferrable desc distinct do else end except false fetch for foreign freeze from full grant
    group having ilike in initially inner intersect into is isnull join lateral leading left like
    limit localtime localtimestamp natural not notnull null offset on only or order outer
    overlaps placing primary references returning right select session_user similar some
    symmetric system_user table tablesample then to trailing true union unique user using
    variadic verbose when where window with""".split()
)
_NAME = re.compile(TABLE_NAME)
_KIND_ORDER = {"EXTENSION": 0, "FUNCTION": 1, "INDEX": 2, "TRIGGER": 3}


@dataclass(frozen=True)
class Declared:
    """What a Workflow's Sub-Skills declare: every table, and the handoff tables' fields."""

    tables: dict[str, set[str]] = field(default_factory=dict)  # table -> skill_ids
    handoff: dict[str, set[str]] = field(default_factory=dict)  # table -> required_fields

    def of(self, skill_id: str) -> set[str]:
        return {t for t, skills in self.tables.items() if skill_id in skills}


def declared(sub_skills: Iterable[Mapping[str, Any]]) -> Declared:
    """From the stored ISATVON 1.4 Sub-Skills (`skill_metadata` + `isatvon_definition`)."""
    out = Declared()
    for sub in sub_skills:
        skill_id = str(sub.get("skill_metadata", {}).get("skill_id", ""))
        definition = sub.get("isatvon_definition", {})
        tables = list(definition.get("v_variables", {}).get("tables", []) or [])
        handoff = definition.get("o_outcome", {}).get("state_handoff_file") or {}
        if isinstance(handoff, dict) and handoff.get("table"):
            tables.append(handoff["table"])
            fields = out.handoff.setdefault(str(handoff["table"]), set())
            fields.update(str(f) for f in handoff.get("required_fields", []) or [])
        for table in tables:
            out.tables.setdefault(str(table), set()).add(skill_id)
    return out


def name_problem(table: str) -> Problem | None:
    if not _NAME.match(table):
        return Problem(
            SchemaFindingCode.INVALID_TABLE_NAME,
            f"table name {table!r} must match {TABLE_NAME}",
            table=table,
        )
    if table in RESERVED:
        return Problem(
            SchemaFindingCode.INVALID_TABLE_NAME,
            f"table name {table!r} is a PostgreSQL reserved word; choose another",
            table=table,
        )
    return None


def columns(statement: Statement) -> list[exp.ColumnDef]:
    schema = statement.tree.this if statement.tree is not None else None
    return [c for c in getattr(schema, "expressions", []) if isinstance(c, exp.ColumnDef)]


def _shape(statement: Statement) -> tuple[str, ...]:
    """A CREATE TABLE compared as the set of its column and constraint definitions."""
    schema = statement.tree.this if statement.tree is not None else None
    return tuple(sorted(e.sql(dialect="postgres") for e in getattr(schema, "expressions", [])))


def _references(statement: Statement) -> set[str]:
    if statement.tree is None:
        return set()
    own = statement.table
    return {
        t.name.lower()
        for ref in statement.tree.find_all(exp.Reference)
        for t in ref.find_all(exp.Table)
        if t.name.lower() != own
    }


def _kind(statement: Statement) -> str:
    if isinstance(statement.tree, exp.Create):
        return str(statement.tree.args.get("kind") or "").upper()
    return "EXTENSION" if statement.text.upper().startswith("CREATE EXTENSION") else "FUNCTION"


def _key(statement: Statement) -> str:
    if isinstance(statement.tree, exp.Create):
        return statement.tree.sql(dialect="postgres")
    return " ".join(statement.text.split())


def _ordered(tables: dict[str, Statement]) -> list[str]:
    """Referenced tables first, ties (and cycles) by name."""
    pending = {t: _references(s) & tables.keys() for t, s in tables.items()}
    out: list[str] = []
    while pending:
        ready = sorted(t for t, deps in pending.items() if not deps - set(out))
        nxt = ready[0] if ready else sorted(pending)[0]
        out.append(nxt)
        del pending[nxt]
    return out


@dataclass(frozen=True)
class Merged:
    sql: str
    tables: list[str]  # every declared table with valid name, sorted
    problems: list[Problem]


def merge(declared: Declared, base: Script | None, fragments: Mapping[str, Script]) -> Merged:
    """`fragments` by skill_id. Scripts with problems are expected to be rejected before this."""
    problems: list[Problem] = []
    valid = set()
    for table in sorted(declared.tables):
        problem = name_problem(table)
        if problem is None:
            valid.add(table)
        else:
            problems.append(problem)

    base_tables = base.tables() if base is not None else {}
    generated: dict[str, Statement] = {}
    others: dict[str, Statement] = {}
    for skill_id in sorted(fragments):
        for statement in fragments[skill_id].statements:
            name = statement.table
            if name is None:
                others.setdefault(_key(statement), statement)
                continue
            known = base_tables.get(name) or generated.get(name)
            if known is None:
                generated[name] = statement
            elif _shape(known) != _shape(statement):
                problems.append(
                    Problem(
                        SchemaFindingCode.CONFLICT,
                        f"table {name} is defined differently by {skill_id} and an earlier "
                        f"definition",
                        statement.index,
                        name,
                    )
                )

    defined = base_tables.keys() | generated.keys()
    for table in sorted(valid - defined):
        skills = ", ".join(sorted(declared.tables[table]))
        problems.append(
            Problem(
                SchemaFindingCode.MISSING_TABLE,
                f"{table} (declared by {skills}) has no CREATE TABLE",
                table=table,
            )
        )
    for table in sorted(declared.handoff.keys() & valid & defined):
        statement = base_tables.get(table) or generated[table]
        have = {c.name.lower() for c in columns(statement)}
        missing = sorted(declared.handoff[table] - have)
        if missing:
            problems.append(
                Problem(
                    SchemaFindingCode.MISSING_TABLE,
                    f"handoff table {table} lacks required field(s): {', '.join(missing)}",
                    table=table,
                )
            )

    statements = [s.text for s in (base.statements if base is not None else [])]
    statements += [generated[t].text for t in _ordered(generated)]
    order = sorted(others, key=lambda k: (_KIND_ORDER.get(_kind(others[k]), 9), k))
    statements += [others[k].text for k in order]
    statements += guard_sql(declared.handoff.keys() & valid & defined)
    # no tables at all: the empty script (its sha256 is the manifest's empty-schema hash)
    sql = "".join(f"{s.rstrip().rstrip(';')};\n\n" for s in statements).rstrip() + "\n" * bool(
        statements
    )
    return Merged(sql=sql, tables=sorted(valid), problems=problems)
