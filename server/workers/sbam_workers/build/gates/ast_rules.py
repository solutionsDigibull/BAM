"""The AST gate's rule checks (E12.3, TD §12.5): decoupling, no manual loop, handoff, imports, SQL.

Like `ast_checks`, pure functions over parsed trees: nothing is imported or executed. They run
on the assembled files after AST_SYNTAX passed, and judge the code against the template's own
rules, not against the skeleton (AST_SKELETON already pinned everything outside the slots).

* AST_DECOUPLING: the action and check tools share only `models` and `state` (ISATVON rule 4).
  Neither imports, names or calls the other, and nothing reaches a sibling through `sys.modules`.
* AST_NO_MANUAL_LOOP: iteration is declarative (the Temporal loop is the retry). No `while`, no
  retry decorator or library, no recursion, no unbounded iterator, no sleep, no retry-in-a-`for`.
  A bounded `for` over data is fine.
* AST_HANDOFF: the action function itself calls `state_handoff.append(...)` with every
  manifest `required_fields` key as a keyword, and nothing writes the handoff table any other way
  (no UPDATE/DELETE/DDL against it, no reaching into the writer's private connection).
* AST_IMPORTS: imports are within the template's base imports plus the registry-resolved
  libraries, siblings relative; never eval/exec/compile/__import__, `os.system`, pickle, marshal,
  ctypes, importlib, and subprocess only when declared. Dynamic attribute access that could
  smuggle one of those past the name checks is refused too.
* AST_SQL_SAFETY: SQL is a literal, parameterised with `%s`. An f-string, `+`, `%`, `.format` or
  `.join` that builds SQL text is refused, and a tool file executes only a literal.

Findings are conservative: when a rule cannot prove a construct safe it fails closed, and the
correction loop (E13) asks the model to write it plainly.
"""

import ast
import re
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sbam_contracts.build_dashboard import GateName

from sbam_workers.build.codegen.bodies import (
    _SQL,
    DECLARABLE_MODULES,
    FORBIDDEN_ATTRS,
    FORBIDDEN_MODULES,
    FORBIDDEN_NAMES,
    RETRY_MODULES,
    SIBLING_MODULES,
    import_allowlist,
)
from sbam_workers.build.codegen.render import identifier
from sbam_workers.build.codegen.store import LoadedTemplate
from sbam_workers.build.gates.ast_checks import CheckResult, Finding, _result

ACTION_FILE: Final = "action_tool.py"
CHECK_FILE: Final = "check_tool.py"
TOOL_FILES: Final = (ACTION_FILE, CHECK_FILE)
RETRY_NAMES: Final = frozenset(
    {"retry", "retrying", "backoff", "tenacity", "retry_if", "on_exception"}
)
UNBOUNDED_ITERATORS: Final = frozenset({"count", "cycle", "repeat"})
SLEEPS: Final = frozenset({"sleep"})
# Beyond the pre-check's list: direct file/terminal access has no place in a tool (the project's
# files go through StorageBackend, its data through the DB), and `breakpoint()` runs pdb.
GATE_FORBIDDEN_NAMES: Final = FORBIDDEN_NAMES | {"open", "breakpoint", "input"}
ALLOWED_DUNDERS: Final = frozenset({"__name__", "__doc__"})
DYNAMIC_ACCESS: Final = frozenset({"getattr", "setattr", "delattr", "globals", "locals", "vars"})
EXECUTE_METHODS: Final = frozenset(
    {"execute", "executemany", "executescript", "exec_driver_sql", "mogrify"}
)
STRING_BUILDERS: Final = frozenset(
    {"format", "format_map", "join", "replace", "expandtabs", "translate", "__mod__", "__add__"}
)
_WRITES: Final = re.compile(r"\b(update|delete|truncate|drop|alter|replace|merge)\b", re.I)
# A Name or attribute the rules treat as "the handoff writer module".
HANDOFF_MODULE: Final = "state_handoff"

CODES: Final = {
    GateName.AST_DECOUPLING: ["CROSS_IMPORT"],
    GateName.AST_NO_MANUAL_LOOP: [
        "WHILE_LOOP",
        "MANUAL_LOOP",
        "RECURSION",
        "RETRY_CONSTRUCT",
        "RETRY_DECORATOR",
        "UNBOUNDED_ITERATION",
    ],
    GateName.AST_HANDOFF: [
        "HANDOFF_FIELD_MISSING",
        "APPEND_MISSING",
        "HANDOFF_PRIVATE_ACCESS",
        "HANDOFF_MUTATION",
    ],
    GateName.AST_IMPORTS: [
        "DYNAMIC_ACCESS",
        "FORBIDDEN_CALL",
        "FORBIDDEN_IMPORT",
        "IMPORT_NOT_ALLOWED",
    ],
    GateName.AST_SQL_SAFETY: ["SQL_NOT_LITERAL", "DYNAMIC_SQL"],
}


def _snippet(node: ast.AST) -> str | None:
    """The offending code, first line only and at most 200 chars (the issue's `snippet`)."""
    try:
        lines = ast.unparse(node).splitlines()
    except (ValueError, RecursionError):
        return None
    return lines[0][:200] if lines else None


def _at(gate: GateName, code: str, message: str, file: str, node: ast.AST) -> Finding:
    line, col = getattr(node, "lineno", 1), getattr(node, "col_offset", 0)
    return Finding(gate, code, message, file, line, col, actual=_snippet(node))


def _top(name: str | None) -> str:
    return (name or "").split(".", 1)[0]


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


# --- AST_DECOUPLING -----


def check_decoupling(trees: Mapping[str, ast.Module], params: Mapping[str, Any]) -> list[Finding]:
    gate = GateName.AST_DECOUPLING
    names = {
        ACTION_FILE: (identifier(params["action_tool"]), "action_tool"),
        CHECK_FILE: (identifier(params["fixed_check_tool"]), "check_tool"),
    }
    findings: list[Finding] = []
    for file, other_file in ((ACTION_FILE, CHECK_FILE), (CHECK_FILE, ACTION_FILE)):
        tree = trees.get(file)
        if tree is None:
            continue
        other_fn, other_module = names[other_file]
        own_fn = names[file][0]
        banned = {other_fn, other_module} - {own_fn}
        for node in ast.walk(tree):
            problem: str | None = None
            if isinstance(node, ast.Import):
                hits = [a.name for a in node.names if banned & set(a.name.split("."))]
                problem = f"imports {hits[0]!r}" if hits else None
            elif isinstance(node, ast.ImportFrom):
                parts = set((node.module or "").split("."))
                parts |= {a.name for a in node.names}
                if banned & parts:
                    problem = f"imports {sorted(banned & parts)[0]!r}"
                elif any(a.name == "*" for a in node.names):
                    problem = "uses a star import, which hides what it couples to"
                elif node.level > 1:
                    problem = "imports from outside its package"
            elif isinstance(node, ast.Name) and node.id in banned:
                problem = f"refers to {node.id!r}"
            elif isinstance(node, ast.Attribute) and (
                node.attr in banned
                or (node.attr == "modules" and _dotted(node.value) == "sys")
            ):
                problem = f"refers to {node.attr!r}"
            elif isinstance(node, ast.Constant) and node.value in banned:
                problem = f"names {node.value!r} as a string"
            if problem:
                message = f"{file} {problem}: the action and check tools must stay independent"
                findings.append(_at(gate, "CROSS_IMPORT", message, file, node))
    return findings


# --- AST_NO_MANUAL_LOOP -----


def _retry_decorator(deco: ast.expr) -> bool:
    inner = deco.func if isinstance(deco, ast.Call) else deco
    return any(p.lower() in RETRY_NAMES for p in _dotted(inner).split(".")) or any(
        _top(_dotted(inner)) == m for m in RETRY_MODULES
    )


def _retry_in_for(loop: ast.For | ast.AsyncFor) -> bool:
    """`for _ in range(n): try: ... except: continue/sleep` is a retry loop in disguise."""
    for node in ast.walk(loop):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for sub in ast.walk(handler):
                    if isinstance(sub, ast.Continue):
                        return True
    return False


def _recursive(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Functions that can reach themselves through bare-name calls to functions of this file."""
    fns = _functions(tree)
    names = {f.name for f in fns}
    calls: dict[str, set[str]] = {n: set() for n in names}
    for fn in fns:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in names:
                    calls[fn.name].add(node.func.id)

    def reaches(start: str) -> bool:
        seen: set[str] = set()
        todo = list(calls[start])
        while todo:
            current = todo.pop()
            if current == start:
                return True
            if current not in seen:
                seen.add(current)
                todo.extend(calls[current])
        return False

    return [f for f in fns if reaches(f.name)]


def check_no_manual_loop(trees: Mapping[str, ast.Module]) -> list[Finding]:
    gate = GateName.AST_NO_MANUAL_LOOP
    findings: list[Finding] = []
    for file, tree in sorted(trees.items()):
        for fn in _functions(tree):
            for deco in fn.decorator_list:
                if _retry_decorator(deco):
                    message = f"{fn.name!r} is wrapped in a retry decorator; retries are Temporal's"
                    findings.append(_at(gate, "RETRY_DECORATOR", message, file, deco))
        for fn in _recursive(tree):
            message = f"{fn.name!r} calls itself, directly or through others: that is a manual loop"
            findings.append(_at(gate, "RECURSION", message, file, fn))
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                message = "`while` is a manual loop; iterate declaratively over data"
                findings.append(_at(gate, "WHILE_LOOP", message, file, node))
            elif isinstance(node, ast.For | ast.AsyncFor):
                if isinstance(node, ast.AsyncFor):
                    message = "`async for` may never end; iterate declaratively over data"
                    findings.append(_at(gate, "MANUAL_LOOP", message, file, node))
                if _retry_in_for(node):
                    message = "a `for` whose except branch continues is a retry loop"
                    findings.append(_at(gate, "RETRY_CONSTRUCT", message, file, node))
            elif isinstance(node, ast.Call):
                name = _call_name(node)
                if name in UNBOUNDED_ITERATORS and _top(_dotted(node.func)) in (
                    "itertools",
                    name,
                ):
                    message = f"{name}() is an unbounded iterator"
                    findings.append(_at(gate, "UNBOUNDED_ITERATION", message, file, node))
                elif name == "iter" and len(node.args) == 2:
                    message = "iter(callable, sentinel) loops until the sentinel appears"
                    findings.append(_at(gate, "UNBOUNDED_ITERATION", message, file, node))
                elif name in SLEEPS:
                    message = f"{name}() waits between attempts; retries are Temporal's"
                    findings.append(_at(gate, "RETRY_CONSTRUCT", message, file, node))
            elif isinstance(node, ast.Import | ast.ImportFrom):
                modules = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for module in modules:
                    if _top(module) in RETRY_MODULES:
                        message = f"{module!r} is a retry library; retries are Temporal's"
                        findings.append(_at(gate, "RETRY_CONSTRUCT", message, file, node))
    return findings


# --- AST_HANDOFF -----


def _never(test: ast.expr) -> bool:
    return isinstance(test, ast.Constant) and not test.value


def _reachable(block: list[ast.stmt]) -> Iterable[ast.stmt]:
    """Statements of `block` that can run: stops after a return/raise/break/continue, and skips
    the body of `if <constant false>` and the else of `if <constant true>`."""
    for stmt in block:
        yield stmt
        if isinstance(stmt, ast.Return | ast.Raise | ast.Break | ast.Continue):
            return


def _live_nodes(block: list[ast.stmt]) -> Iterable[ast.AST]:
    """Every node that can execute in `block`, not entering nested defs, lambdas or classes."""
    pending: list[ast.AST] = list(reversed(list(_reachable(block))))
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        yield node
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            live = node.orelse if _never(node.test) else node.body
            pending.extend(reversed(list(_reachable(live))))
            continue
        for _, value in ast.iter_fields(node):
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                pending.extend(reversed(list(_reachable(value))))
            elif isinstance(value, list):
                pending.extend(reversed([v for v in value if isinstance(v, ast.AST)]))
            elif isinstance(value, ast.AST):
                pending.append(value)


def _handoff_calls(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """`state_handoff.append(...)` calls that can run in `fn`'s own body."""
    return [
        node
        for node in _live_nodes(fn.body)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == HANDOFF_MODULE
    ]


def check_handoff(trees: Mapping[str, ast.Module], params: Mapping[str, Any]) -> list[Finding]:
    gate = GateName.AST_HANDOFF
    findings: list[Finding] = []
    required = {identifier(f) for f in params["required_fields"]}
    table = str(params["handoff_table"])
    action = trees.get(ACTION_FILE)
    if action is not None:
        name = identifier(params["action_tool"])
        fn = next((f for f in _functions(action) if f.name == name), None)
        if fn is not None:
            calls = _handoff_calls(fn)
            if not calls:
                message = f"{name!r} never calls state_handoff.append(...)"
                findings.append(_at(gate, "APPEND_MISSING", message, ACTION_FILE, fn))
            for call in calls:
                if call.args or any(k.arg is None for k in call.keywords):
                    message = "state_handoff.append takes explicit keywords only (no * or **)"
                    findings.append(_at(gate, "HANDOFF_FIELD_MISSING", message, ACTION_FILE, call))
                    continue
                given = {k.arg for k in call.keywords}
                missing = sorted(required - given)
                if missing:
                    message = f"state_handoff.append is missing required field(s) {missing}"
                    findings.append(_at(gate, "HANDOFF_FIELD_MISSING", message, ACTION_FILE, call))
    for file, tree in sorted(trees.items()):
        if file == "state_handoff.py":
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == HANDOFF_MODULE
                and node.attr not in ("append", "REQUIRED_FIELDS", "TABLE", "SKILL_ID")
            ):
                message = f"state_handoff.{node.attr} is the writer's own; call append() only"
                findings.append(_at(gate, "HANDOFF_PRIVATE_ACCESS", message, file, node))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if table.lower() in text.lower() and _WRITES.search(text):
                    message = f"SQL that changes or drops {table!r}: the handoff is append-only"
                    findings.append(_at(gate, "HANDOFF_MUTATION", message, file, node))
    return findings


# --- AST_IMPORTS -----


def _import_findings(
    file: str, node: ast.Import | ast.ImportFrom, allowed: set[str], declared: set[str]
) -> list[Finding]:
    gate = GateName.AST_IMPORTS
    out: list[Finding] = []
    if isinstance(node, ast.ImportFrom) and node.level:
        tools = {"action_tool", "check_tool"} if file == "server.py" else set()
        siblings = SIBLING_MODULES | tools
        ok = node.level == 1 and (
            (node.module is None and {a.name for a in node.names} <= siblings)
            or (node.module in siblings)
        )
        if not ok:
            message = "relative imports may only name models, state or state_handoff"
            out.append(_at(gate, "IMPORT_NOT_ALLOWED", message, file, node))
        return out
    modules = (
        [a.name for a in node.names]
        if isinstance(node, ast.Import)
        else [node.module or ""]
    )
    for module in modules:
        top = _top(module)
        if top in FORBIDDEN_MODULES or (top in DECLARABLE_MODULES and top not in declared):
            message = f"import of {module!r} is forbidden"
            out.append(_at(gate, "FORBIDDEN_IMPORT", message, file, node))
        elif top not in allowed:
            message = f"import of {module!r} is not in the template's imports or the libraries"
            out.append(_at(gate, "IMPORT_NOT_ALLOWED", message, file, node))
    if isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name in GATE_FORBIDDEN_NAMES | FORBIDDEN_ATTRS:
                message = f"import of {alias.name!r} is forbidden"
                out.append(_at(gate, "FORBIDDEN_IMPORT", message, file, node))
    return out


def check_imports(
    trees: Mapping[str, ast.Module], template: LoadedTemplate, modules: Iterable[str]
) -> list[Finding]:
    gate = GateName.AST_IMPORTS
    declared = set(modules)
    allowed = set(import_allowlist(template.base_imports, declared))
    findings: list[Finding] = []
    for file, tree in sorted(trees.items()):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                findings += _import_findings(file, node, allowed, declared)
            elif isinstance(node, ast.Name) and (
                node.id in GATE_FORBIDDEN_NAMES or node.id in ("__builtins__", "__loader__")
            ):
                message = f"{node.id} is forbidden in generated code"
                findings.append(_at(gate, "FORBIDDEN_CALL", message, file, node))
            elif isinstance(node, ast.Attribute) and (
                node.attr in FORBIDDEN_ATTRS | GATE_FORBIDDEN_NAMES
                or (
                    node.attr.startswith("__")
                    and node.attr.endswith("__")
                    and node.attr not in ALLOWED_DUNDERS
                )
            ):
                message = f".{node.attr} is forbidden in generated code"
                findings.append(_at(gate, "FORBIDDEN_CALL", message, file, node))
            elif isinstance(node, ast.Call) and _call_name(node) in DYNAMIC_ACCESS:
                message = f"{_call_name(node)}() can reach a forbidden name dynamically"
                findings.append(_at(gate, "DYNAMIC_ACCESS", message, file, node))
    return findings


# --- AST_SQL_SAFETY -----


def _is_sql_text(text: str) -> bool:
    return bool(_SQL.search(text))


def _const_text(node: ast.AST) -> str:
    """The literal pieces of a string expression, joined; '' if it has none."""
    return " ".join(
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )


def _literal_strings(tree: ast.Module) -> set[str]:
    """Names bound once to a plain string literal (module level or any scope)."""
    counts: dict[str, int] = {}
    literal: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    counts[target.id] = counts.get(target.id, 0) + 1
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        literal.add(target.id)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign | ast.NamedExpr | ast.For):
            targets = [node.target]
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        counts[sub.id] = counts.get(sub.id, 0) + 2
    return {n for n in literal if counts.get(n, 0) == 1}


def _sql_names(tree: ast.Module) -> set[str]:
    """Names assigned a SQL-looking string literal: building on them is building SQL."""
    return {
        t.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
        and _is_sql_text(n.value.value)
        for t in n.targets
        if isinstance(t, ast.Name)
    }


def _safe_execute_arg(arg: ast.expr, literals: set[str]) -> bool:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return True
    if isinstance(arg, ast.Name):
        return arg.id in literals
    if isinstance(arg, ast.Subscript):  # QUERIES["name"] / state.QUERIES["name"]
        return _dotted(arg.value).split(".")[-1] == "QUERIES"
    return False


def check_sql_safety(trees: Mapping[str, ast.Module]) -> list[Finding]:
    gate = GateName.AST_SQL_SAFETY
    findings: list[Finding] = []
    for file, tree in sorted(trees.items()):
        literals = _literal_strings(tree)
        sql_names = _sql_names(tree)
        for node in ast.walk(tree):
            built: ast.AST | None = None
            if isinstance(node, ast.JoinedStr) and any(
                isinstance(v, ast.FormattedValue) for v in node.values
            ):
                built = node
            elif isinstance(node, ast.BinOp | ast.AugAssign) and isinstance(
                node.op, ast.Add | ast.Mod
            ):
                built = node
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in STRING_BUILDERS
            ):
                built = node
            if built is not None and (
                _is_sql_text(_const_text(built))
                or any(isinstance(n, ast.Name) and n.id in sql_names for n in ast.walk(built))
            ):
                message = "SQL text is built at runtime; use a literal with %s placeholders"
                findings.append(_at(gate, "DYNAMIC_SQL", message, file, built))
            if (
                file in TOOL_FILES
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in EXECUTE_METHODS
            ):
                if node.func.attr == "executescript" or not node.args:
                    message = f"{node.func.attr}() is not allowed in a tool"
                    findings.append(_at(gate, "SQL_NOT_LITERAL", message, file, node))
                elif not _safe_execute_arg(node.args[0], literals):
                    message = "execute() must be given a string literal, not a built value"
                    findings.append(_at(gate, "SQL_NOT_LITERAL", message, file, node))
    return findings


# --- all five -----


def run_rule_checks(
    trees: Mapping[str, ast.Module],
    params: Mapping[str, Any],
    template: LoadedTemplate,
    modules: Iterable[str],
) -> tuple[CheckResult, ...]:
    """DECOUPLING, NO_MANUAL_LOOP, HANDOFF, IMPORTS, SQL_SAFETY over parsed trees, in that order."""
    return (
        _result(GateName.AST_DECOUPLING, check_decoupling(trees, params)),
        _result(GateName.AST_NO_MANUAL_LOOP, check_no_manual_loop(trees)),
        _result(GateName.AST_HANDOFF, check_handoff(trees, params)),
        _result(GateName.AST_IMPORTS, check_imports(trees, template, modules)),
        _result(GateName.AST_SQL_SAFETY, check_sql_safety(trees)),
    )
