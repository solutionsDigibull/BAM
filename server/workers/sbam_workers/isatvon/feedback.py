"""#929 (WG-41): what the model is told when its Sub-Skill fails the ISATVON validator.

A bare code told the model nothing, so the real model repeated `no_op_condition` on an output,
left out `Action:`/`Check:`/`Bound:` and wrote `float` as a JSON-schema type on every attempt. A
line here names the position (`sub_skills[0]...path`), the validator's code, the offending entry
NAMES when there are any (a gate identifier, an output field), and one fixed sentence that says
how to fix it.

Only codes, paths, identifiers and the fixed sentences below go out. Never the validator's message
(it can quote a value) and never document text (E1.21): an identifier is sent only when it is a
plain name (`IDENTIFIER`), and an output field only when the model's own `json_schema` defines it.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from sbam_contracts.api import ErrorCode
from sbam_contracts.isatvon14 import IDENTIFIER, LOOP_COUNTER_NAMES, output_fields

# The short form of `o_outcome.json_schema`; the validator accepts exactly these names.
TYPE_NAMES: Final = ("string", "integer", "number", "boolean", "object", "array")

_QUOTED = re.compile(r"'([^']{1,80})'")
_MARKER = re.compile(r"missing '(Triggered by|Action:|Check:|Bound:)'")

NO_OP: Final = (
    "no_op_condition runs before the first pass: use only names from required_inputs, "
    "fresh_state_parameters or max_iterations, never an output field of json_schema and never "
    "attempt or iteration; if none fits write `max_iterations < 1`"
)
OTHER_GATE: Final = (
    "success_gate and escalation_condition may use output fields, required_inputs, "
    "fresh_state_parameters and max_iterations; every name must be declared there"
)
GATE_GRAMMAR: Final = (
    "a gate is bare names compared with == != < <= > >= or is, joined by AND, OR, NOT and "
    "parentheses; no function calls, no lists, no `in`, no `output[...]` or dots"
)
LOOP_COUNTER: Final = (
    "there is no attempt counter: the loop already stops at max_iterations; remove it from "
    "fresh_state_parameters (`[]` is valid) and from every gate"
)
MARKERS: Final = {
    "Triggered by": "the statement begins `Triggered by <the event that starts it>.`",
    "Action:": "add `Action: <the one job the action tool does>.`",
    "Check:": "add `Check: <what the independent check tool verifies>.`",
    "Bound:": "add `Bound: Up to <max_iterations> passes, then escalate to <role>.`",
}
TYPES: Final = (
    "every json_schema property is one of " + ", ".join(TYPE_NAMES) + " (or a list of allowed "
    "strings, or an object with a `type` among them); never float, int, String, list or a status "
    "word such as Priced"
)
BOUND: Final = "the `bound` text must contain the number max_iterations, for example `3 passes`"
BY_SUFFIX: Final = {
    "i_instructions.bound": BOUND,
    "t_tech_stack.fixed_check_tool": "fixed_check_tool must be a different tool from action_tool",
    "n_notification.ui_updates": (
        "at least two ui_updates: one `Looping [Pass x/y]` and one that says Escalated, "
        "Completed, Done or Resolved"
    ),
    "max_iterations": "max_iterations is a whole number from 1 to 10",
    "budget_limit": "budget_limit is `<n>ms` or `<n>s`, for example `5s`",
    "state_handoff_file.table": (
        "the handoff table is a lowercase snake_case SQL name listed in v_variables.tables"
    ),
    "state_handoff_file.required_fields": "state_handoff_file.required_fields needs one field",
    "state_handoff_file.mode": "state_handoff_file.mode is `append_only`",
}
# A stored finding, never asked again:
SKIPPED_CODES: Final = frozenset({ErrorCode.SECRET_VALUE_NOT_ALLOWED.value})


def _name(message: str) -> str | None:
    """The identifier a gate finding names (`'status' is an output ...`), if it is a plain name."""
    match = _QUOTED.search(message)
    return match.group(1) if match and IDENTIFIER.match(match.group(1)) else None


def _bad_types(raw: Mapping[str, Any]) -> list[str]:
    """The output properties whose type is not an allowed name: names only, never the values."""
    try:
        schema = raw["isatvon_definition"]["o_outcome"]["json_schema"]
        properties = schema["properties"] if schema.get("type") == "object" else schema
    except (KeyError, TypeError, AttributeError):
        return []
    if not isinstance(properties, Mapping):
        return []
    bad: list[str] = []
    for name, spec in properties.items():
        ok = (isinstance(spec, str) and spec in TYPE_NAMES) or (
            isinstance(spec, list) and bool(spec) and all(isinstance(v, str) for v in spec)
        )
        ok = ok or (isinstance(spec, Mapping) and isinstance(spec.get("type"), str))
        if not ok and isinstance(name, str) and IDENTIFIER.match(name):
            bad.append(name)
    return bad


def _is_output(raw: Mapping[str, Any], name: str) -> bool:
    try:
        return name in output_fields(raw["isatvon_definition"]["o_outcome"]["json_schema"])
    except (KeyError, TypeError, AttributeError):
        return False


def explain(raw: Mapping[str, Any], finding: Mapping[str, Any]) -> str:
    """`CODE rule n: detail (fixed fix)` for one stored finding of `raw` (codes and names only)."""
    code = str(finding.get("code") or "")
    path = str(finding.get("path") or "")
    message = str(finding.get("message") or "")
    rule = finding.get("rule")
    head = f"{code} rule {rule}" if isinstance(rule, int) else code
    leaf = path.rsplit(".", 1)[-1]
    detail, fix = "", None
    if code == "UNRESOLVED_IDENTIFIER":
        name = _name(message)
        if name:
            kind = (
                "a loop counter"
                if name.lower() in LOOP_COUNTER_NAMES
                else "an output" if _is_output(raw, name) else "undeclared"
            )
            detail = f" {name} ({kind})"
        fix = NO_OP if leaf == "no_op_condition" else OTHER_GATE
    elif code == "GATE_PARSE_ERROR":
        fix = GATE_GRAMMAR
    elif "fresh_state_parameters" in path and "loop counter" in message:
        name = _name(message)
        detail = f" {name}" if name else ""
        fix = LOOP_COUNTER
    elif path.endswith("i_instructions.statement"):
        marker = _MARKER.search(message)
        if marker:
            detail = f" missing {marker.group(1)}"
            fix = MARKERS[marker.group(1)]
    elif path.endswith("o_outcome.json_schema"):
        bad = _bad_types(raw)
        detail = f" properties {', '.join(bad)}" if bad else ""
        fix = TYPES
    else:
        fix = next((text for key, text in BY_SUFFIX.items() if path.endswith(key)), None)
    return f"{head}{detail}" + (f" ({fix})" if fix else "")


def lines(
    raws: Sequence[Mapping[str, Any]], findings: Iterable[Sequence[Mapping[str, Any]]]
) -> list[str]:
    """Re-ask lines for the Sub-Skills of one answer (`findings[i]` belong to `raws[i]`)."""
    out: list[str] = []
    for k, (raw, found) in enumerate(zip(raws, findings, strict=True)):
        for finding in found:
            path = str(finding.get("path") or "")
            if str(finding.get("code")) in SKIPPED_CODES or path.startswith("skill_metadata."):
                continue  # SBAM sets the ids and redacts secrets itself
            out.append(f"sub_skills[{k}].sub_skill.{path}: {explain(raw, finding)}")
    return list(dict.fromkeys(out))
