"""AST gates for Spike E0.11 (#29), TD §12.5.

Each gate returns {check, passed, findings: [{file, line, message}]}. They run on the assembled
files before any static gate or sandbox run:

  AST_SKELETON        everything outside the slots is AST-identical to a fresh render (T06)
  AST_CONFORMANCE     tool functions + params match the manifest, both registered, check returns
                      every gate output, no slot redefines a tool (T06)
  AST_DECOUPLING      check tool never imports/calls the action tool, and vice versa (T04)
  AST_NO_MANUAL_LOOP  no `while`, no retry decorators or retry libraries (T05)
  AST_HANDOFF         action calls state_handoff.append(...) with every required field; slot SQL is
                      read-only (T07)
"""

from __future__ import annotations

import ast
import re
import textwrap
from typing import Any

from codegen.assemble import replace_slot, slot_contents
from codegen.render import pyident

PYTYPE = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}
RETRY_NAMES = {"retry", "retrying", "tenacity", "backoff", "stamina"}
WRITE_SQL = re.compile(r"\b(UPDATE|DELETE|INSERT|DROP|ALTER|TRUNCATE|CREATE|GRANT)\b", re.I)
SLOT_LINE = re.compile(r"^\s*# (>>>|<<<) LLM_BODY:(\w+)\s*$", re.M)

Finding = dict[str, Any]


def _result(check: str, findings: list[Finding]) -> dict[str, Any]:
    return {"check": check, "passed": not findings, "findings": findings}


def _f(file: str, line: int, message: str) -> Finding:
    return {"file": file, "line": line, "message": message}


def slot_lines(code: str) -> set[int]:
    """1-based line numbers inside any slot (between its markers)."""
    lines, inside = set(), False
    for no, text in enumerate(code.splitlines(), start=1):
        m = SLOT_LINE.match(text)
        if m:
            inside = m.group(1) == ">>>"
        elif inside:
            lines.add(no)
    return lines


def _func(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# --- AST_SKELETON --------------------------------------------------------------------------------


def ast_skeleton(
    files: dict[str, str], skeleton: dict[str, str], spec: dict[str, Any]
) -> dict[str, Any]:
    findings: list[Finding] = []
    for f in spec["files"]:
        name, slots = f["output"], list(f["slots"])
        code = files.get(name)
        if code is None:
            findings.append(_f(name, 0, "file missing from the bundle"))
            continue
        markers = [(m.group(1), m.group(2)) for m in SLOT_LINE.finditer(code)]
        expected = [(k, s) for s in slots for k in (">>>", "<<<")]
        if markers != expected:
            findings.append(_f(name, 0, f"slot markers {markers} != template {expected}"))
            continue
        restored = code
        for slot in slots:
            placeholder = textwrap.dedent(slot_contents(skeleton[name], slot)[0])
            restored = replace_slot(restored, slot, placeholder)
        try:
            got, want = ast.parse(restored), ast.parse(skeleton[name])
        except SyntaxError as e:
            findings.append(_f(name, e.lineno or 0, f"does not parse: {e.msg}"))
            continue
        if ast.dump(got) == ast.dump(want):
            continue
        for g, w in zip(got.body, want.body, strict=False):
            if ast.dump(g) != ast.dump(w):
                findings.append(_f(name, g.lineno, "protected region differs from fresh render"))
                break
        else:
            findings.append(_f(name, 0, "number of top-level statements differs from render"))
    return _result("AST_SKELETON", findings)


# --- AST_CONFORMANCE -----------------------------------------------------------------------------


def _params(fn: ast.FunctionDef) -> list[tuple[str, str]]:
    return [(a.arg, ast.unparse(a.annotation) if a.annotation else "") for a in fn.args.args]


def _registered(tree: ast.Module) -> list[tuple[str, int]]:
    """Names passed to @mcp.tool(name=...) in server.py."""
    out = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and ast.unparse(d.func) == "mcp.tool":
                for kw in d.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        out.append((str(kw.value.value), node.lineno))
    return out


def ast_conformance(files: dict[str, str], ctx: dict[str, Any]) -> dict[str, Any]:
    findings: list[Finding] = []
    want = [(pyident(i["name"]), PYTYPE[i["type"]]) for i in ctx["inputs"]]
    tools = {
        "action_tool.py": pyident(ctx["action_tool"]),
        "check_tool.py": pyident(ctx["fixed_check_tool"]),
    }
    for file, tool in tools.items():
        tree = ast.parse(files[file])
        fn = _func(tree, tool)
        if fn is None:
            findings.append(_f(file, 0, f"tool function {tool}() not found at module level"))
            continue
        if _params(fn) != want:
            findings.append(_f(file, fn.lineno, f"{tool} params {_params(fn)} != manifest {want}"))
        inside = slot_lines(files[file])
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if node.lineno in inside and node.name in tools.values():
                findings.append(_f(file, node.lineno, f"slot redefines tool {node.name}()"))
        if file == "check_tool.py":
            findings += _check_returns(file, fn, ctx["gate_outputs"])
    registered = _registered(ast.parse(files["server.py"]))
    if sorted(n for n, _ in registered) != sorted(tools.values()):
        msg = f"registered tools {registered} != {sorted(tools.values())}"
        findings.append(_f("server.py", 0, msg))
    findings += _queries_cover(files["state.py"], ctx["fresh_state"])
    return _result("AST_CONFORMANCE", findings)


def _queries_cover(code: str, fresh_state: list[str]) -> list[Finding]:
    """state.QUERIES must have one query per fresh-state name (S.fresh_state_parameters)."""
    for node in ast.parse(code).body:
        if isinstance(node, ast.AnnAssign) and ast.unparse(node.target) == "QUERIES":
            if not isinstance(node.value, ast.Dict):
                return [_f("state.py", node.lineno, "QUERIES must be a dict literal")]
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            missing = [s for s in fresh_state if pyident(s) not in keys]
            if missing:
                return [_f("state.py", node.lineno, f"QUERIES missing fresh state {missing}")]
            return []
    return [_f("state.py", 0, "QUERIES not found")]


def _check_returns(file: str, fn: ast.FunctionDef, gate_outputs: list[str]) -> list[Finding]:
    findings = []
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    if not returns:
        findings.append(_f(file, fn.lineno, "check tool never returns"))
    for r in returns:
        if not isinstance(r.value, ast.Dict):
            findings.append(_f(file, r.lineno, "check tool must return a dict literal"))
            continue
        keys = {k.value for k in r.value.keys if isinstance(k, ast.Constant)}
        missing = [g for g in gate_outputs if g not in keys]
        if missing:
            findings.append(_f(file, r.lineno, f"return is missing gate outputs {missing}"))
    return findings


# --- AST_DECOUPLING ------------------------------------------------------------------------------


def _couplings(file: str, code: str, module: str, tool: str) -> list[Finding]:
    findings = []
    for node in ast.walk(ast.parse(code)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.ImportFrom):
            names = {a.name for a in node.names}
            if (node.module or "").split(".")[-1] == module or module in names or tool in names:
                findings.append(_f(file, line, f"imports from the {module} module"))
        elif isinstance(node, ast.Import):
            if any(a.name.split(".")[-1] == module for a in node.names):
                findings.append(_f(file, line, f"imports the {module} module"))
        elif isinstance(node, ast.Name) and node.id in (module, tool):
            findings.append(_f(file, line, f"references {node.id}"))
        elif isinstance(node, ast.Attribute) and node.attr == tool:
            findings.append(_f(file, line, f"references {node.attr}"))
    return findings


def ast_decoupling(files: dict[str, str], ctx: dict[str, Any]) -> dict[str, Any]:
    action, check = pyident(ctx["action_tool"]), pyident(ctx["fixed_check_tool"])
    findings = _couplings("check_tool.py", files["check_tool.py"], "action_tool", action)
    findings += _couplings("action_tool.py", files["action_tool.py"], "check_tool", check)
    return _result("AST_DECOUPLING", findings)


# --- AST_NO_MANUAL_LOOP --------------------------------------------------------------------------


def ast_no_manual_loop(files: dict[str, str]) -> dict[str, Any]:
    findings: list[Finding] = []
    for file, code in files.items():
        for node in ast.walk(ast.parse(code)):
            line = getattr(node, "lineno", 0)
            if isinstance(node, ast.While):
                findings.append(_f(file, line, "while loop (the runner owns iterations)"))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for d in node.decorator_list:
                    root = ast.unparse(d).split("(")[0].split(".")
                    if RETRY_NAMES & set(root):
                        findings.append(_f(file, d.lineno, f"retry decorator @{'.'.join(root)}"))
            elif isinstance(node, ast.Import | ast.ImportFrom):
                mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                if any(m.split(".")[0] in RETRY_NAMES for m in mods):
                    findings.append(_f(file, line, "imports a retry library"))
    return _result("AST_NO_MANUAL_LOOP", findings)


# --- AST_HANDOFF ---------------------------------------------------------------------------------


def ast_handoff(files: dict[str, str], ctx: dict[str, Any]) -> dict[str, Any]:
    findings: list[Finding] = []
    required = ctx["required_fields"]
    tree = ast.parse(files["action_tool.py"])
    fn = _func(tree, pyident(ctx["action_tool"]))
    calls = [
        n
        for n in ast.walk(fn or tree)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "state_handoff.append"
    ]
    if not calls:
        line = fn.lineno if fn else 0
        msg = f"state_handoff.append(...) is never called; required_fields: {', '.join(required)}"
        findings.append(_f("action_tool.py", line, msg))
    for call in calls:
        given = {kw.arg for kw in call.keywords}
        missing = [r for r in required if r not in given]
        if None in given:
            findings.append(_f("action_tool.py", call.lineno, "append(**...) hides the fields"))
        elif missing:
            findings.append(_f("action_tool.py", call.lineno, f"append() missing fields {missing}"))
    for file in ("action_tool.py", "check_tool.py", "state.py"):
        inside = slot_lines(files[file])
        for node in ast.walk(ast.parse(files[file])):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.lineno in inside and WRITE_SQL.search(node.value):
                    findings.append(_f(file, node.lineno, f"write SQL in a slot: {node.value!r}"))
    return _result("AST_HANDOFF", findings)


def run_all(
    files: dict[str, str], skeleton: dict[str, str], ctx: dict[str, Any], spec: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        ast_skeleton(files, skeleton, spec),
        ast_conformance(files, ctx),
        ast_decoupling(files, ctx),
        ast_no_manual_loop(files),
        ast_handoff(files, ctx),
    ]
