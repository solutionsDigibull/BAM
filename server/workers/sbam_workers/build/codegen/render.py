"""Render a fastmcp-loopy skeleton from a released template (TD §12.3, E11.1).

Pipeline: manifest -> params (params.py) -> validate -> context -> Jinja2 -> 6 Python files.

* Jinja2 `SandboxedEnvironment` with `StrictUndefined`, autoescape off (the output is Python).
* Manifest prose reaches the output only through `python_literal` (any value as a Python
  literal) and `docstring` (text safe inside a `\"\"\"` docstring); identifiers only through
  `identifier`, which re-validates. A template cannot print a raw manifest string.
* Rendering is a pure function of (template bytes, params): no clock, no randomness, no dict
  ordering that depends on anything but the input. Same input, same bytes (E11.1-T01).
* Nothing is written until every file rendered: `write_skeleton` only takes a finished result.
"""

import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Final

import jinja2
from jinja2.sandbox import SandboxedEnvironment
from pydantic import ValidationError
from sbam_contracts.manifest import SkillManifest, TemplateRef

from sbam_workers.build.codegen.errors import TemplateError, TemplateErrorCode
from sbam_workers.build.codegen.params import (
    check_identifier,
    invalid,
    params_from_manifest,
    validate_params,
)
from sbam_workers.build.codegen.store import LoadedTemplate

_SCALAR_TYPES: Final = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}


# --- filters -------------------------------------------------------------------------------------


def str_literal(value: str) -> str:
    """`repr` of a string, double-quoted (the repo's Black style); every escape is repr's."""
    text = repr(value)
    if text[0] == '"':
        return text
    # repr chose single quotes, so the body has only `\\'` escapes and raw double quotes.
    return '"' + text[1:-1].replace("\\'", "'").replace('"', '\\"') + '"'


def python_literal(value: object) -> str:
    """A str / int / float / bool / None as a Python literal (`repr`, which escapes everything)."""
    if isinstance(value, str):
        return str_literal(value)
    if value is None or isinstance(value, bool | int):
        return repr(value)
    if isinstance(value, float) and isfinite(value):
        return repr(value)
    raise invalid([f"cannot render {type(value).__name__} {value!r} as a Python literal"])


def docstring(value: object) -> str:
    """Text that is safe between triple double quotes.

    Escapes the backslash and every double quote (so `\"\"\"` can never close the docstring),
    and every control, format, surrogate or separator character except the newline (so NUL, bidi
    overrides and U+2028 never appear raw). Newlines stay: the template's word-wrap re-indents them.
    """
    if not isinstance(value, str):
        raise invalid([f"docstring text must be a string, got {type(value).__name__}"])
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch != "\n" and unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Zl", "Zp", "Co", "Cn"}:
            code = ord(ch)
            if code < 0x100:
                out.append(f"\\x{code:02x}")
            elif code < 0x10000:
                out.append(f"\\u{code:04x}")
            else:
                out.append(f"\\U{code:08x}")
        else:
            out.append(ch)
    return "".join(out)


def identifier(value: object) -> str:
    """Return `value` only if it is a valid, non-keyword identifier; never a raw string."""
    return check_identifier(value, "identifier")


def environment(template: LoadedTemplate) -> SandboxedEnvironment:
    """Sandboxed, strict, no autoescape; templates come from the hashed bytes, not the disk."""
    env = SandboxedEnvironment(
        loader=jinja2.DictLoader(dict(template.sources)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,  # the output is Python source; escaping is done by the filters
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["python_literal"] = python_literal
    env.filters["docstring"] = docstring
    env.filters["identifier"] = identifier
    return env


# --- context -------------------------------------------------------------------------------------


def _annotation(field: Mapping[str, Any], names: set[str]) -> str:
    """Python annotation for a schema field; adds the typing names it needs to `names`."""
    kind = field["type"]
    values = field.get("enum")
    if values is not None:
        names.add("Literal")
        return "Literal[" + ", ".join(python_literal(v) for v in values) + "]"
    if kind == "object":
        names.add("Any")
        return "dict[str, Any]"
    if kind == "array":
        item = field.get("items")
        if item is None:
            names.add("Any")
            return "list[Any]"
        return f"list[{_SCALAR_TYPES[item]}]"
    return _SCALAR_TYPES[kind]


def build_context(params: Mapping[str, Any], template: LoadedTemplate) -> dict[str, Any]:
    """Everything the templates read (see `context` per file in template.yaml)."""
    model_names: set[str] = set()
    inputs = [
        {"name": f["name"], "annotation": _annotation(f, model_names), "required": f["required"]}
        for f in params["inputs"]
    ]
    outputs = [
        {"name": f["name"], "annotation": _annotation(f, model_names), "required": f["required"]}
        for f in params["outputs"]
    ]
    required_first = [f for f in inputs if f["required"]] + [
        f for f in inputs if not f["required"]
    ]
    tool_names: set[str] = {"Any"}  # every tool returns dict[str, Any]
    for f in params["inputs"]:
        _annotation(f, tool_names)
    key_field = next(f["name"] for f in inputs if f["required"])
    return {
        "skill_id": params["skill_id"],
        "template_version": template.version,
        "inputs": inputs,
        "outputs": outputs,
        "signature": required_first,
        "key_field": key_field,
        "typing_imports": {"models": sorted(model_names), "tools": sorted(tool_names)},
        "parameters": list(params["parameters"]),
        "fresh_state": list(params["fresh_state"]),
        "gate_outputs": list(params["gate_outputs"]),
        "handoff_table": params["handoff_table"],
        "required_fields": list(params["required_fields"]),
        "action_tool": params["action_tool"],
        "action_step": params["action_step"],
        "fixed_check_tool": params["fixed_check_tool"],
        "acceptance_check": params["acceptance_check"],
    }


# --- render --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderedSkeleton:
    """The six rendered files (output name -> text, in template order) and the template used."""

    files: Mapping[str, str]
    template: TemplateRef


def _check_isatvon(template: LoadedTemplate, isatvon_version: str) -> None:
    if isatvon_version not in template.supports_isatvon:
        raise TemplateError(
            TemplateErrorCode.TEMPLATE_UNSUPPORTED,
            f"{template.name} {template.version} supports ISATVON "
            f"{list(template.supports_isatvon)}, not {isatvon_version!r}",
        )


def render_skeleton(
    template: LoadedTemplate, params: Mapping[str, Any], *, isatvon_version: str = "1.4"
) -> RenderedSkeleton:
    """Validate `params` and render every file. Raises TemplateError; returns all or nothing."""
    _check_isatvon(template, isatvon_version)
    validate_params(template, params)
    context = build_context(params, template)
    env = environment(template)
    rendered: dict[str, str] = {}
    for entry in template.files:
        try:
            text = env.get_template(entry.template).render(**context)
        except jinja2.TemplateError as exc:  # undefined variable, syntax error, sandbox violation
            raise TemplateError(
                TemplateErrorCode.TEMPLATE_RENDER_ERROR,
                f"{entry.template}: {type(exc).__name__}: {exc}",
            ) from exc
        if "\r" in text:
            raise TemplateError(
                TemplateErrorCode.TEMPLATE_RENDER_ERROR, f"{entry.template}: output contains CR"
            )
        rendered[entry.output] = text
    return RenderedSkeleton(files=rendered, template=template.ref)


def render_manifest(
    template: LoadedTemplate, manifest: SkillManifest | Mapping[str, Any]
) -> RenderedSkeleton:
    """Map a Skill Manifest to params and render. A malformed manifest is TEMPLATE_PARAM_INVALID."""
    if not isinstance(manifest, SkillManifest):
        try:
            manifest = SkillManifest.model_validate(manifest)
        except ValidationError as exc:
            findings = [
                f"manifest.{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            ]
            raise invalid(findings) from exc
    version = manifest.sbam_build_record.isatvon_framework_version
    _check_isatvon(template, version)
    return render_skeleton(template, params_from_manifest(manifest), isatvon_version=version)


def write_skeleton(skeleton: RenderedSkeleton, out_dir: Path) -> list[Path]:
    """Write the rendered files (UTF-8, LF). Only callable with a fully rendered skeleton."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, text in skeleton.files.items():
        path = out_dir / name
        path.write_bytes(text.encode("utf-8"))
        written.append(path)
    return written


def params_json(params: Mapping[str, Any]) -> str:
    """Canonical JSON of render params (golden fixtures, logs)."""
    return json.dumps(params, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
