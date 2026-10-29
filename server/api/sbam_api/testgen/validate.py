"""What the API checks about a case before it is stored (E13.4): the server-side rules the contract
cannot express. Each problem is a `FieldError` whose `path` points into the request body, so the UI
can mark the field (`/input/amount`, `/initial_state/0`).

* `input` validates against the Sub-Skill's `contracts.input_schema` (built from its ISATVON, the
  same `build_contracts` the manifest uses) and carries no field the Sub-Skill does not declare.
* The seed SQL (`initial_state`, `state_script`) passes the statement allowlist and, together,
  writes at most `MAX_SEED_ROWS` rows.
* `expected_terminal` is one the case's kind can reach.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator
from sbam_contracts.api import FieldError
from sbam_contracts.isatvon14 import SubSkill as SubSkillModel
from sbam_contracts.isatvon14 import build_contracts
from sbam_contracts.testgen import KIND_TERMINAL, MAX_SEED_ROWS, CaseBody, CaseKind

from sbam_api.testgen import seed_sql

_QUOTED = re.compile(r"'([^']+)'")


def input_schema(isatvon: Mapping[str, Any]) -> dict[str, Any]:
    """The Sub-Skill's `contracts.input_schema`, closed (an input it does not declare is a typo)."""
    schema: dict[str, Any] = build_contracts(SubSkillModel.model_validate(isatvon))["input_schema"]
    return {**schema, "additionalProperties": False}


def declared_tables(isatvon: Mapping[str, Any]) -> list[str]:
    tables: list[str] = SubSkillModel.model_validate(isatvon).isatvon_definition.v_variables.tables
    return tables


def _input_errors(schema: Mapping[str, Any], value: Mapping[str, Any]) -> list[FieldError]:
    errors: list[FieldError] = []
    for error in sorted(Draft202012Validator(dict(schema)).iter_errors(dict(value)), key=str):
        parts = [str(p) for p in error.absolute_path]
        if error.validator in ("required", "additionalProperties"):
            # these report the object; the field is named in the message
            parts += _QUOTED.findall(error.message)[:1]
        path = "/input" + "".join(f"/{p}" for p in parts)
        errors.append(FieldError(path=path, message=error.message))
    return errors


def _seed_errors(field: str, statements: Sequence[str], tables: Sequence[str]) -> list[FieldError]:
    return [
        FieldError(path=f"/{field}/{index}", message=reason)
        for index, reason in seed_sql.check_statements(statements, tables)
    ]


def case_errors(isatvon: Mapping[str, Any], kind: CaseKind, case: CaseBody) -> list[FieldError]:
    """Every problem with a case, empty when it may be stored."""
    tables = declared_tables(isatvon)
    errors = _input_errors(input_schema(isatvon), case.input)
    errors += _seed_errors("initial_state", case.initial_state, tables)
    for step, statements in enumerate(case.state_script):
        errors += [
            FieldError(
                path=f"/state_script/{step}{e.path.removeprefix('/state_script')}",
                message=e.message,
            )
            for e in _seed_errors("state_script", statements, tables)
        ]
    if not errors:  # the row budget is only meaningful for statements that are all allowed
        every = [*case.initial_state, *(sql for step in case.state_script for sql in step)]
        if seed_sql.count_rows(every, tables) > MAX_SEED_ROWS:
            errors.append(
                FieldError(
                    path="/initial_state",
                    message=f"a case may write at most {MAX_SEED_ROWS} rows in total",
                )
            )
    if case.expected_terminal not in KIND_TERMINAL[kind]:
        allowed = ", ".join(sorted(t.value for t in KIND_TERMINAL[kind]))
        errors.append(
            FieldError(
                path="/expected_terminal",
                message=(
                    f"a {kind.value} case ends in {allowed}, " f"not {case.expected_terminal.value}"
                ),
            )
        )
    return errors
