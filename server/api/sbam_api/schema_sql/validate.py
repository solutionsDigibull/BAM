"""SCHEMA_SQL parse + statement allowlist (E9.4, TD §12.6). Pure: no database, no I/O. Shared by
the build worker (`build_schema_sql`) and `PUT W/schema-sql/source`, which checks an upload.

A schema (the FDE's uploaded base or an LLM fragment) is data. It is split into statements with
sqlglot's PostgreSQL tokenizer (so `;` inside `$$ … $$` bodies, strings and comments doesn't split),
each statement is parsed, and only foundational DDL is allowed:

- `CREATE TABLE` / `CREATE INDEX` / `CREATE TRIGGER` (sqlglot parses these);
- `CREATE [OR REPLACE] FUNCTION … LANGUAGE plpgsql|sql` whose body uses none of `FORBIDDEN_WORDS`
  (sqlglot keeps a function as an opaque `Command`, so its text is checked);
- `CREATE EXTENSION` of an `EXTENSIONS` entry.

Everything else (DROP, ALTER, COPY, GRANT/ROLE, DO blocks, INSERT/UPDATE/DELETE, views, …) is
FORBIDDEN_STATEMENT. The apply step runs in a throwaway database on an isolated server anyway; the
allowlist keeps the schema shippable and refuses what could reach outside that database.
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

import sqlglot
from sbam_contracts.schema_sql import SchemaFindingCode
from sqlglot import TokenType, exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import ParseError, TokenError

# sqlglot warns "Falling back to parsing as a 'Command'" for every function / extension; expected.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

EXTENSIONS = frozenset({"pgcrypto", "uuid-ossp", "citext"})
# Never allowed anywhere in a statement's text (a function body included): dynamic SQL, server-side
# file and program access, other databases, privileges.
FORBIDDEN_WORDS = re.compile(
    r"\b(?:EXECUTE\b(?!\s+(?:FUNCTION|PROCEDURE)\b)|PROGRAM\b|ALTER\s+SYSTEM\b|COPY\b|"
    r"pg_read_(?:binary_)?file\b|pg_ls_dir\b|pg_stat_file\b|lo_import\b|lo_export\b|dblink\w*|"
    r"SECURITY\s+DEFINER\b|SET\s+ROLE\b|SET\s+SESSION\s+AUTHORIZATION\b|GRANT\b|REVOKE\b)",
    re.IGNORECASE,
)
_FUNCTION = re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b", re.IGNORECASE)
_LANGUAGE = re.compile(r"\bLANGUAGE\s+'?(\w+)'?", re.IGNORECASE)
_EXTENSION = re.compile(
    r"^CREATE\s+EXTENSION\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?([\w-]+)\"?\s*$", re.IGNORECASE
)
_ALLOWED_KINDS = frozenset({"TABLE", "INDEX", "TRIGGER"})


@dataclass(frozen=True)
class Problem:
    """One SCHEMA_SQL finding before it becomes a `build_dashboard.Finding`."""

    code: SchemaFindingCode
    message: str
    statement_index: int | None = None  # 0-based, in the script it was found in
    table: str | None = None


@dataclass(frozen=True)
class Statement:
    index: int
    text: str
    tree: exp.Expr | None  # None: not parsed (a parse error)

    @property
    def table(self) -> str | None:
        """The table a CREATE TABLE defines, lower-cased; else None."""
        if isinstance(self.tree, exp.Create) and self.tree.args.get("kind") == "TABLE":
            return self.tree.find(exp.Table).name.lower()  # type: ignore[union-attr]
        return None


@dataclass
class Script:
    statements: list[Statement] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def tables(self) -> dict[str, Statement]:
        return {s.table: s for s in self.statements if s.table is not None}


def split(sql: str) -> Iterator[str]:
    """Top-level statements, comments kept, empty ones dropped. A tokenizer error (e.g. an
    unterminated string or `$$`) yields the rest as one statement for the parser to reject."""
    try:
        tokens = Postgres().tokenize(sql)
    except TokenError:
        if sql.strip():
            yield sql.strip()
        return
    start = 0
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            chunk = sql[start : token.start]
            start = token.end + 1
            if _has_code(chunk):
                yield chunk.strip()
    if _has_code(sql[start:]):
        yield sql[start:].strip()


def _has_code(chunk: str) -> bool:
    try:
        return bool(Postgres().tokenize(chunk))
    except TokenError:
        return bool(chunk.strip())


def _allowed(text: str, tree: exp.Expr) -> str | None:
    """None when the statement is allowed, else why not."""
    found = FORBIDDEN_WORDS.search(text)
    if found:
        return f"{' '.join(found.group(0).split()).upper()} is not allowed"
    if isinstance(tree, exp.Create):
        kind = str(tree.args.get("kind") or "").upper()
        if kind not in _ALLOWED_KINDS:
            return f"CREATE {kind or '?'} is not allowed"
        if kind == "TABLE" and (tree.expression is not None or tree.args.get("properties")):
            return "CREATE TABLE must list its columns (no AS SELECT, no options)"
        return None
    if isinstance(tree, exp.Command):
        if _FUNCTION.match(text):
            language = _LANGUAGE.search(text)
            if language is None or language.group(1).lower() not in {"plpgsql", "sql"}:
                return "a function must be LANGUAGE plpgsql or sql"
            return None
        extension = _EXTENSION.match(text)
        if extension:
            if extension.group(1).lower() in EXTENSIONS:
                return None
            return f"extension {extension.group(1)} is not allowed"
    return f"{' '.join(text.split()[:2]).upper()} is not allowed"


def check(sql: str) -> Script:
    """Split, parse and allowlist one script. Statement indexes are 0-based."""
    script = Script()
    for index, text in enumerate(split(sql)):
        try:
            [tree] = sqlglot.parse(text, dialect="postgres")
        except (ParseError, TokenError, ValueError) as exc:
            first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            script.statements.append(Statement(index, text, None))
            script.problems.append(Problem(SchemaFindingCode.PARSE_ERROR, first, index))
            continue
        script.statements.append(Statement(index, text, tree))
        if tree is None:
            continue
        reason = _allowed(text, tree)
        if reason is not None:
            script.problems.append(Problem(SchemaFindingCode.FORBIDDEN_STATEMENT, reason, index))
    return script
