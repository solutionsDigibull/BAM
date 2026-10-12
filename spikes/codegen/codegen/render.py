"""Safe skeleton renderer for Spike E0.11 (#29), TD §12.3.

The templates (platform-owned, in Git) are the only Jinja2 *source*. Manifest text is always
passed in as *data* and reaches the generated Python only through these filters:

  pylit      -> a Python string literal ('...'); repr() escapes quotes, backslashes, newlines
  docstring  -> text that is safe inside a triple-quoted docstring (quotes, backslashes escaped)
  pyident    -> a valid Python identifier
                ("Auth_MCP (Session & OTP Evaluator)" -> auth_mcp_session_otp_evaluator)
  pyvalue    -> a Python literal for a V parameter value (bool, int, float or str only)

So Jinja syntax inside the manifest ("{{ 7*7 }}") is never evaluated, and a triple quote in the
manifest cannot close a docstring early (E0.11-T03). The environment is sandboxed (templates cannot
reach Python internals) and uses StrictUndefined (a missing manifest field is an error, never an
empty string). Output is LF-only and hashed with sha256, so the same manifest always gives the
same bytes (E0.11-T01).

Self-check:  cd server && uv run --with jinja2==3.1.6 python ../spikes/codegen/codegen/render.py
"""

from __future__ import annotations

import ast
import hashlib
import keyword
import re
import sys
from pathlib import Path
from typing import Any

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # control chars except \t and \n


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} expects a string, got {type(value).__name__}")
    if _CONTROL.search(value):
        raise ValueError(f"{name}: control characters are not allowed in manifest text")
    return value


def pylit(value: object) -> str:
    """Manifest text -> Python string literal. repr() escapes everything that could break out."""
    return repr(_text(value, "pylit"))


def docstring(value: object) -> str:
    """Manifest text -> docstring body. Escaping every quote means no run of three can form."""
    return _text(value, "docstring").replace("\\", "\\\\").replace('"', '\\"')


def pyident(value: object) -> str:
    """Manifest name -> snake_case identifier. Fails loudly instead of guessing on bad input."""
    ident = re.sub(r"[^0-9a-zA-Z]+", "_", _text(value, "pyident")).strip("_").lower()
    if not ident.isidentifier() or keyword.iskeyword(ident) or ident[0].isdigit():
        raise ValueError(f"pyident: cannot make an identifier from {value!r}")
    return ident


def pyvalue(value: object) -> str:
    """Manifest scalar (V parameter value) -> Python literal. Only bool/int/float/str."""
    if isinstance(value, bool | int | float):
        return repr(value)
    return pylit(value)


def make_env(template_dir: Path | None = None) -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        loader=FileSystemLoader(str(template_dir)) if template_dir else None,
        undefined=StrictUndefined,
        autoescape=False,  # we generate Python, not HTML; escaping is done by the filters above
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(pylit=pylit, docstring=docstring, pyident=pyident, pyvalue=pyvalue)
    return env


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def render_string(source: str, context: dict[str, Any]) -> str:
    """Render a template given as a string (used by the self-check and tests)."""
    return _lf(make_env().from_string(source).render(**context))


def render_file(env: SandboxedEnvironment, name: str, context: dict[str, Any]) -> str:
    """Render one template file from the template directory."""
    return _lf(env.get_template(name).render(**context))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- self-check ---------------------------------------------------------------------------

_DEMO = '''def {{ tool | pyident }}() -> str:
    """{{ step | docstring }}"""
    return {{ step | pylit }}
'''
# E0.11-T03 payload, as it might appear in a manifest's action_step
_INJECTION = '{{ 7*7 }} """ import os'


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  ->  {detail}" if detail else ""))
    return ok


def main() -> int:
    ctx = {"tool": "Auth_MCP (Session & OTP Evaluator)", "step": _INJECTION}
    out = render_string(_DEMO, ctx)
    print("---- rendered ----\n" + out + "------------------")

    not_evaluated = "49" not in out and "{{ 7*7 }}" in out
    results = [
        _check("T03 Jinja syntax in manifest is NOT evaluated", not_evaluated),
        _check("T03 triple quote cannot close the docstring", '\\"\\"\\"' in out),
    ]
    try:
        fn = ast.parse(out).body[0]
        if not isinstance(fn, ast.FunctionDef):
            raise SyntaxError("expected a function definition")
        doc = ast.get_docstring(fn)
        results.append(_check("T03 output is valid Python (ast.parse)", True, f"{fn.name}()"))
        results.append(
            _check("T03 docstring holds the payload literally", doc == _INJECTION, repr(doc))
        )
    except SyntaxError as e:
        results.append(_check("T03 output is valid Python (ast.parse)", False, str(e)))

    h1, h2 = sha256_text(out), sha256_text(render_string(_DEMO, ctx))
    results.append(_check("T01 two renders are byte-identical", h1 == h2, h1[:16] + "..."))

    try:
        render_string("{{ missing_field }}", {})
        results.append(_check("StrictUndefined rejects a missing field", False))
    except Exception as e:  # show whatever Jinja raises
        results.append(_check("StrictUndefined rejects a missing field", True, type(e).__name__))

    try:
        render_string("{{ ''.__class__.__mro__[1].__subclasses__() }}", {})
        results.append(_check("sandbox blocks access to Python internals", False))
    except Exception as e:
        results.append(_check("sandbox blocks access to Python internals", True, type(e).__name__))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
