"""LLM-proposed DDL for the tables the base schema doesn't define (E9.4, TD §12.6).

One `sbam-codegen` call per Sub-Skill (prompt `schema-ddl`), for the tables it declares that
neither the base nor an earlier Sub-Skill (by skill_id) has defined, so two Sub-Skills never race
to define one table. The answer is checked like any schema (`validate.check`) plus: only
`CREATE TABLE` / `CREATE INDEX`, exactly the requested tables, every handoff `required_field`.
Failures are re-asked (at most 3, `llm/reask.py`); after that the last attempt's errors become
findings and nothing is applied (T06).
"""

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field
from sbam_api.schema_sql.validate import Problem, Script, check
from sbam_contracts.schema_sql import SchemaFindingCode

from sbam_workers.build.schema_sql.merge import columns
from sbam_workers.llm.reask import SchemaInvalidAfterRetries

ALIAS: Final = "sbam-codegen"
PROMPT: Final = "schema-ddl"
_LLM_KINDS = frozenset({"TABLE", "INDEX"})
_CODE = re.compile(r"^([A-Z_]+)\b")


class DdlFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=200_000)


def fragment_errors(
    out: DdlFragment, wanted: set[str], handoff: Mapping[str, set[str]]
) -> tuple[str, ...]:
    """Re-ask lines: a finding code first, then statement index / table names only."""
    script = check(out.sql)
    errors = [f"{p.code} statement {p.statement_index}: {p.message}" for p in script.problems]
    for statement in script.statements:
        tree = statement.tree
        kind = str(tree.args.get("kind") or "").upper() if tree is not None else ""
        if tree is not None and kind not in _LLM_KINDS:
            errors.append(
                f"{SchemaFindingCode.FORBIDDEN_STATEMENT} statement {statement.index}: "
                "only CREATE TABLE and CREATE INDEX"
            )
    tables = script.tables()
    errors += [f"{SchemaFindingCode.MISSING_TABLE} {t}" for t in sorted(wanted - tables.keys())]
    extra = sorted(tables.keys() - wanted)
    errors += [f"{SchemaFindingCode.CONFLICT} {t} was not requested" for t in extra]
    for table in sorted(wanted & tables.keys() & handoff.keys()):
        have = {c.name.lower() for c in columns(tables[table])}
        for missing in sorted(handoff[table] - have):
            errors.append(f"{SchemaFindingCode.MISSING_TABLE} {table}.{missing} (required field)")
    return tuple(errors)


# (context, validate) -> the validated output; the activity binds the LlmClient, alias, prompt
# and replay scope.
Ask = Callable[
    [Mapping[str, str], Callable[[DdlFragment], tuple[str, ...]]], Awaitable[DdlFragment]
]


async def propose(
    ask: Ask,
    sub_skill: Mapping[str, Any],
    wanted: set[str],
    handoff: Mapping[str, set[str]],
    existing: set[str],
) -> tuple[Script | None, list[Problem]]:
    """The checked fragment, or the findings of its last failed attempt."""
    mine = {t: sorted(f) for t, f in handoff.items() if t in wanted}
    context = {
        "sub_skill": json.dumps(sub_skill, sort_keys=True),
        "tables": json.dumps(sorted(wanted)),
        "handoff": json.dumps(mine, sort_keys=True),
        "existing_tables": json.dumps(sorted(existing)),
    }
    try:
        out = await ask(context, lambda o: fragment_errors(o, wanted, handoff))
    except SchemaInvalidAfterRetries as exc:
        lines = exc.attempts[-1].errors if exc.attempts else ("model output never validated",)
        return None, [_problem(line) for line in lines]
    return check(out.sql), []


def _problem(line: str) -> Problem:
    match = _CODE.match(line)
    code = (
        SchemaFindingCode(match.group(1))
        if match and match.group(1) in SchemaFindingCode.__members__
        else SchemaFindingCode.PARSE_ERROR
    )
    return Problem(code, f"generated DDL: {line}")
