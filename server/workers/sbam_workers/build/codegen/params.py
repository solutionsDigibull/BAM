"""Render params for the fastmcp-loopy skeleton (TD §12.3).

`params_from_manifest` maps a validated Skill Manifest to the params the template declares in
`params_schema`. `validate_params` is the gate in front of the renderer: JSON Schema first, then
the rules a schema cannot express (Python keywords and builtins, names the generated modules
already use, Pydantic-reserved field names, enum/type agreement). Every failure is a
TEMPLATE_PARAM_INVALID that lists all findings. Nothing renders until this passes (E11.1-T05).

Source of values: `sbam_build_record.isatvon_detail` (the structured ISATVON 1.4 form: V
parameters and fresh state are identifiers there) and `contracts`. The human tool names in the
manifest ("Auth_MCP (Session & OTP Evaluator)") become snake_case identifiers here; any other
manifest text reaches the code only through the escaping filters in render.py.
"""

import builtins
import keyword
import re
from collections.abc import Mapping
from math import isfinite
from typing import Any, Final

from jsonschema import Draft202012Validator
from pydantic import BaseModel
from sbam_contracts.gate_expr import ExpressionError, identifiers, parse
from sbam_contracts.manifest import SkillManifest

from sbam_workers.build.codegen.errors import TemplateError, TemplateErrorCode
from sbam_workers.build.codegen.store import LoadedTemplate

IDENTIFIER: Final = re.compile(r"[a-z_][a-z0-9_]*")
# Names the generated modules already bind (imports, locals, the constants are upper case).
_RESERVED: Final = frozenset(
    "state state_handoff models action_tool check_tool server mcp current annotations json "
    "fields payload args key".split()
)
# PostgreSQL reserved words: a handoff table cannot be named one of them without quoting.
_SQL_RESERVED: Final = frozenset(
    "all analyse analyze and any array as asc asymmetric both case cast check collate column "
    "constraint create current_catalog current_date current_role current_time current_timestamp "
    "current_user default deferrable desc distinct do else end except false fetch for foreign "
    "from grant group having in initially intersect into lateral leading limit localtime "
    "localtimestamp not null offset on only or order placing primary references returning "
    "select session_user some symmetric table then to trailing true union unique user using "
    "variadic when where window with insert update delete drop alter truncate".split()
)
_PYDANTIC_RESERVED: Final = frozenset(dir(BaseModel))
_BUILTINS: Final = frozenset(dir(builtins))
_SCALARS: Final = ("string", "integer", "number", "boolean")
_NON_ALNUM: Final = re.compile(r"[^0-9a-zA-Z]+")


def invalid(findings: list[str]) -> TemplateError:
    shown = "; ".join(findings[:5]) + (f" (+{len(findings) - 5} more)" if len(findings) > 5 else "")
    return TemplateError(TemplateErrorCode.TEMPLATE_PARAM_INVALID, shown, findings)


def check_text(value: object, where: str) -> str:
    """Manifest prose that will sit inside a docstring or a literal: it must be encodable.

    Control characters are legal here: the `docstring` and `python_literal` filters escape them,
    so a NUL or a bidi override never reaches the generated source as a raw character.
    """
    if not isinstance(value, str):
        raise invalid([f"{where}: expected text, got {type(value).__name__}"])
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise invalid([f"{where}: text is not valid Unicode (lone surrogate)"]) from exc
    return value


def check_identifier(value: object, where: str) -> str:
    """`^[a-z_][a-z0-9_]*$` (fullmatch: `$` would accept a trailing newline), not a keyword."""
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise invalid([f"{where}: {value!r} is not a valid identifier ^[a-z_][a-z0-9_]*$"])
    if keyword.iskeyword(value) or value.startswith("__"):
        raise invalid([f"{where}: {value!r} is a Python keyword or dunder name"])
    return value


def tool_name(text: str) -> str:
    """Human tool name -> snake_case identifier: "Auth_MCP (OTP & Session)" -> auth_mcp_otp_session.

    Mapping only. The result is validated like any other identifier, so an empty or numeric-first
    result is rejected by `validate_params` instead of being repaired here.
    """
    return _NON_ALNUM.sub("_", text).strip("_").lower()


def _field_specs(schema: Mapping[str, Any], where: str) -> list[dict[str, Any]]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise invalid([f"{where}: properties must be an object"])
    required = set(schema.get("required") or [])
    fields: list[dict[str, Any]] = []
    for name, spec in properties.items():
        if not isinstance(spec, Mapping):
            raise invalid([f"{where}.{name}: property must be an object"])
        field: dict[str, Any] = {
            "name": name,
            "type": spec.get("type"),
            "required": name in required,
        }
        if "enum" in spec:
            field["enum"] = spec["enum"]
        items = spec.get("items")
        if isinstance(items, Mapping) and "type" in items:
            field["items"] = items["type"]
        fields.append(field)
    return fields


def params_from_manifest(manifest: SkillManifest) -> dict[str, Any]:
    """Skill Manifest -> render params. Does not validate; `validate_params` does."""
    data = manifest.dump()
    return params_from_parts(
        data["skill_metadata"]["skill_id"],
        data["sbam_build_record"]["isatvon_detail"],
        data["contracts"],
    )


def params_from_parts(
    skill_id: str, detail: Mapping[str, Any], contracts: Mapping[str, Any]
) -> dict[str, Any]:
    """The render params from the three things a manifest supplies: the skill id, the structured
    ISATVON (`isatvon_detail`) and the `contracts`. A manifest is not needed to get them."""
    tech, auto, variables, outcome = (
        detail[k] for k in ("t_tech_stack", "a_automation", "v_variables", "o_outcome")
    )
    handoff = outcome["state_handoff_file"]
    inputs = _field_specs(contracts["input_schema"], "contracts.input_schema")
    parameters = [
        {"name": p["name"], "value": p["value"] if p.get("value") is not None else p.get("default")}
        for p in variables.get("parameters") or []
    ]
    fresh_state = list(detail["s_source"]["fresh_state_parameters"])
    # Names the gates use that are not inputs, V bounds or fresh state: only the fixed check
    # tool can produce them, so it must return every one (AST_CONFORMANCE).
    try:
        used = identifiers(parse(variables["success_gate"])) | identifiers(
            parse(variables["escalation_condition"])
        )
    except ExpressionError as exc:
        raise invalid([f"v_variables gate expression: {exc}"]) from exc
    known = {f["name"] for f in inputs} | {"max_iterations"} | {p["name"] for p in parameters}
    return {
        "skill_id": skill_id,
        "action_tool": tool_name(tech["action_tool"]),
        "action_step": auto["action_step"],
        "fixed_check_tool": tool_name(tech["fixed_check_tool"]),
        "acceptance_check": auto["acceptance_check"],
        "inputs": inputs,
        "outputs": _field_specs(contracts["output_schema"], "contracts.output_schema"),
        "parameters": parameters,
        "fresh_state": fresh_state,
        "gate_outputs": sorted(used - known - set(fresh_state)),
        "handoff_table": handoff["table"],
        "required_fields": list(handoff["required_fields"]),
    }


def _schema_findings(template: LoadedTemplate, params: Mapping[str, Any]) -> list[str]:
    validator = Draft202012Validator(template.params_schema)
    errors = sorted(validator.iter_errors(params), key=lambda e: [str(p) for p in e.absolute_path])
    return [
        f"{'.'.join(str(p) for p in e.absolute_path) or '<params>'}: {e.message[:200]}"
        for e in errors
    ]


def _unique(names: list[str], where: str, findings: list[str]) -> None:
    for name in sorted({n for n in names if names.count(n) > 1}):
        findings.append(f"{where}: duplicate name {name!r}")


def _enum_findings(field: Mapping[str, Any], where: str) -> list[str]:
    values = field.get("enum")
    if values is None:
        return []
    kind = field["type"]
    if kind == "string":
        ok = all(isinstance(v, str) for v in values)
    elif kind == "integer":
        ok = all(isinstance(v, int) and not isinstance(v, bool) for v in values)
    else:
        return [f"{where}: enum is only supported for string and integer fields"]
    found = [] if ok else [f"{where}: enum values must all be of type {kind}"]
    if len(set(map(repr, values))) != len(values):
        found.append(f"{where}: enum has duplicate values")
    for value in values:
        if isinstance(value, str):
            try:
                check_text(value, f"{where}.enum")
            except TemplateError as exc:
                found.extend(exc.findings)
    return found


def _identifier_findings(params: Mapping[str, Any]) -> list[str]:
    """Every identifier, re-checked in code: the schema pattern's `$` accepts a trailing newline
    and says nothing about keywords (E11.1-T05)."""
    where_value: list[tuple[str, str]] = [
        ("action_tool", params["action_tool"]),
        ("fixed_check_tool", params["fixed_check_tool"]),
        ("handoff_table", params["handoff_table"]),
    ]
    for section in ("inputs", "outputs"):
        where_value += [(f"{section}.{f['name']}", f["name"]) for f in params[section]]
    for section in ("fresh_state", "required_fields", "gate_outputs"):
        where_value += [(f"{section}.{n}", n) for n in params[section]]
    where_value += [(f"parameters.{p['name']}", p["name"]) for p in params["parameters"]]
    findings: list[str] = []
    for where, value in where_value:
        try:
            check_identifier(value, where)
        except TemplateError as exc:
            findings.extend(exc.findings)
    return findings


def _semantic_findings(params: Mapping[str, Any]) -> list[str]:
    findings = _identifier_findings(params)
    if findings:  # reserved-name rules below assume well-formed identifiers
        return findings

    def named(value: str, where: str, *, extra_reserved: frozenset[str] = frozenset()) -> None:
        if value in _RESERVED or value in extra_reserved:
            findings.append(f"{where}: {value!r} is a name the generated modules already use")

    for key in ("action_tool", "fixed_check_tool"):
        where, value = key, params[key]
        named(value, where)
        if value in _BUILTINS:
            findings.append(f"{where}: {value!r} shadows a Python builtin")
    if params["handoff_table"] in _SQL_RESERVED:
        findings.append(f"handoff_table: {params['handoff_table']!r} is a reserved SQL word")
    if "\n" in params["skill_id"]:
        findings.append("skill_id: must be a single line")
    if params["action_tool"] == params["fixed_check_tool"]:
        findings.append("action_tool and fixed_check_tool must differ (ISATVON rule 4)")
    clash = {params["action_tool"], params["fixed_check_tool"]} & {
        f["name"] for f in params["inputs"]
    }
    for name in sorted(clash):
        findings.append(f"tool name {name!r} is also an input name")

    for section in ("inputs", "outputs"):
        fields = params[section]
        _unique([f["name"] for f in fields], section, findings)
        for field in fields:
            where, name = f"{section}.{field['name']}", field["name"]
            named(name, where)
            findings.extend(_enum_findings(field, where))
            if name.startswith("_") or name.startswith("model_") or name in _PYDANTIC_RESERVED:
                findings.append(f"{where}: {name!r} is reserved by Pydantic models")
            if field["type"] != "array" and "items" in field:
                findings.append(f"{where}: items is only valid for array fields")
    if not any(f["required"] for f in params["inputs"]):
        findings.append("inputs: at least one required input is needed to key the fresh state")

    for section in ("fresh_state", "required_fields", "gate_outputs"):
        _unique(list(params[section]), section, findings)
        for name in params[section]:
            named(name, f"{section}.{name}")
            if section == "fresh_state" and (
                name.startswith("_") or name.startswith("model_") or name in _PYDANTIC_RESERVED
            ):
                findings.append(f"{section}.{name}: reserved by Pydantic models")
    _unique([p["name"] for p in params["parameters"]], "parameters", findings)
    for param in params["parameters"]:
        value = param["value"]
        if isinstance(value, float) and not isfinite(value):
            findings.append(f"parameters.{param['name']}: NaN and Infinity are not allowed")
        if isinstance(value, str):
            try:
                check_text(value, f"parameters.{param['name']}")
            except TemplateError as exc:
                findings.extend(exc.findings)
    for key in ("skill_id", "action_step", "acceptance_check"):
        try:
            check_text(params[key], key)
        except TemplateError as exc:
            findings.extend(exc.findings)
    return findings


def validate_params(template: LoadedTemplate, params: Mapping[str, Any]) -> None:
    """Raise TEMPLATE_PARAM_INVALID (with every finding) unless `params` can be rendered safely."""
    findings = _schema_findings(template, params)
    if not findings:  # the semantic rules assume the shape the schema guarantees
        findings = _semantic_findings(params)
    if findings:
        raise invalid(findings)
