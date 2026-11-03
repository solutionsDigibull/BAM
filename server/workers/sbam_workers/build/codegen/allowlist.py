"""The allowlist layer of the bodies pre-check (audit A-02, #428).

The first pre-check was a denylist: a handful of names and attributes. A denylist loses to
indirection (`getattr(__builtins__, "ev" + "al")`, `().__class__.__base__...`, `open(...)`,
`globals()`), so this layer says what a tool body *may* use and rejects the rest:

  * node types: the constructs a deterministic tool needs; classes, `global`/`nonlocal`, `del`,
    generators, `match` and the like are refused (`DISALLOWED_SYNTAX`);
  * builtins: a name that resolves to a builtin must be in `ALLOWED_BUILTINS`; the introspection and
    I/O ones (`getattr`, `open`, `globals`, `iter`, ...) are refused wherever they appear and
    however they are bound (`FORBIDDEN_CALL`);
  * attributes: nothing starting with `_` (every dunder escape goes through one) and no frame or
    code object internals (`PRIVATE_ATTRIBUTE`);
  * size: a literal repeat, `range` or shift whose constant-folded size is past `MAX_ITEMS` is a
    resource bomb (`RESOURCE_BOMB`).

This is a fast fail before assembly, not the boundary: the AST gate runs on the assembled files and
the sandbox contains whatever both miss (docs/adr/0026). It returns plain tuples so it does not
depend on `bodies.py`; the caller turns them into `BodyFinding`s.

Messages name constructs, never the body's text.
"""

import ast
import builtins
from collections.abc import Iterable
from typing import Final

Finding = tuple[str, ast.AST, str]  # (code, node, message)

MAX_ITEMS: Final = 1_000_000  # a repeat or loop past this is a bomb, not a size a tool needs
_HUGE: Final = 1 << 64

ALLOWED_NODES: Final = frozenset(
    {
        # structure
        ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.arguments, ast.arg, ast.Return,
        ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Pass, ast.Break, ast.Continue,
        ast.If, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.withitem, ast.Try,
        ast.ExceptHandler, ast.Raise, ast.Assert, ast.Import, ast.ImportFrom, ast.alias,
        ast.While,  # reported as MANUAL_LOOP by the loop rule, not here
        # expressions
        ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Call, ast.keyword, ast.IfExp,
        ast.Attribute, ast.Subscript, ast.Slice, ast.Starred, ast.Name, ast.Constant,
        ast.JoinedStr, ast.FormattedValue, ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Lambda,
        ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
        ast.NamedExpr, ast.Await,
        # contexts and operators
        ast.Load, ast.Store, ast.And, ast.Or, ast.Not, ast.Invert, ast.UAdd, ast.USub, ast.Add,
        ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.LShift, ast.RShift,
        ast.BitOr, ast.BitXor, ast.BitAnd, ast.MatMult, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
        ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn,
    }
)  # fmt: skip

# What a tool body may use from the builtins. Everything else in `dir(builtins)` is refused when it
# is not bound by the body itself.
ALLOWED_BUILTINS: Final = frozenset(
    {
        "bool", "int", "float", "str", "bytes", "list", "dict", "set", "frozenset", "tuple",
        "len", "min", "max", "sum", "abs", "round", "sorted", "reversed", "range", "enumerate",
        "zip", "map", "filter", "any", "all", "isinstance", "repr", "divmod", "chr", "ord",
        "type", "callable", "hash", "slice",
        "Exception", "ValueError", "KeyError", "TypeError", "IndexError", "RuntimeError",
        "AttributeError", "ArithmeticError", "ZeroDivisionError", "LookupError",
        "NotImplementedError", "AssertionError", "StopIteration",
        "True", "False", "None", "NotImplemented", "Ellipsis",
    }
)  # fmt: skip

# Refused wherever they appear, even if the body binds the name too (`getattr = getattr` at module
# level would otherwise rebind the real builtin and slip past an "is it bound here" test).
CRITICAL_BUILTINS: Final = frozenset(
    {
        "getattr", "setattr", "delattr", "globals", "locals", "vars", "dir", "open", "eval", "exec",
        "compile", "__import__", "breakpoint", "input", "iter", "memoryview", "help", "exit",
        "quit", "__builtins__", "__loader__", "__spec__", "__build_class__",
    }
)  # fmt: skip

# Interpreter internals reachable without an underscore.
INTERNAL_ATTRS: Final = frozenset(
    {
        "gi_frame", "gi_code", "cr_frame", "cr_code", "ag_frame", "ag_code", "tb_frame", "tb_next",
        "f_globals", "f_locals", "f_builtins", "f_back", "f_code", "co_code", "func_globals",
        "func_code", "mro",
    }
)  # fmt: skip

_BUILTINS: Final = frozenset(dir(builtins))
_FOLD_CAP: Final = 1 << 200


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name the body binds anywhere: assignments, parameters, loop and `as` targets, imports,
    defs, comprehension targets."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            bound.add(node.name)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def _fold(node: ast.expr) -> int | None:
    """The integer a constant expression evaluates to, or None when it is not one. Anything past
    `_FOLD_CAP` comes back as the cap, so `10 ** 10 ** 10` is never actually computed."""
    if isinstance(node, ast.Constant):
        value = node.value
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _fold(node.operand)
        return None if inner is None else -inner
    if isinstance(node, ast.BinOp):
        left, right = _fold(node.left), _fold(node.right)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            elif isinstance(node.op, ast.FloorDiv):
                value = left // right
            elif isinstance(node.op, ast.Pow):
                value = _FOLD_CAP if right > 4096 and abs(left) > 1 else left**right
            elif isinstance(node.op, ast.LShift):
                value = _FOLD_CAP if right > 4096 and left else left << right
            else:
                return None
        except (ZeroDivisionError, ValueError, OverflowError):
            return None
        return max(min(value, _FOLD_CAP), -_FOLD_CAP)
    return None


def _is_sequence(node: ast.expr) -> bool:
    return isinstance(node, ast.List | ast.Tuple) or (
        isinstance(node, ast.Constant) and isinstance(node.value, str | bytes)
    )


def check(tree: ast.AST) -> list[Finding]:
    """Every allowlist finding for one parsed body, in source order."""
    bound = _bound_names(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        kind = type(node)
        if kind not in ALLOWED_NODES:
            message = f"{kind.__name__} is not allowed in a body"
            findings.append(("DISALLOWED_SYNTAX", node, message))
            continue
        if isinstance(node, ast.Name):
            findings += _name(node, bound)
        elif isinstance(node, ast.arg) and node.arg in CRITICAL_BUILTINS:
            findings.append(("FORBIDDEN_CALL", node, f"{node.arg!r} cannot be used as a name"))
        elif isinstance(node, ast.Attribute):
            findings += _attribute(node)
        elif isinstance(node, ast.BinOp):
            findings += _bomb_binop(node)
        elif isinstance(node, ast.Call):
            findings += _bomb_range(node)
    findings.sort(key=lambda f: (getattr(f[1], "lineno", 0), getattr(f[1], "col_offset", 0)))
    return findings


def _name(node: ast.Name, bound: set[str]) -> list[Finding]:
    name = node.id
    if name in {"eval", "exec", "compile", "__import__"}:
        return []  # the denylist rule already names these
    if name in CRITICAL_BUILTINS:
        return [("FORBIDDEN_CALL", node, f"builtin {name!r} is not allowed")]
    if name.startswith("__"):
        return [("FORBIDDEN_CALL", node, f"dunder name {name!r} is not allowed")]
    if isinstance(node.ctx, ast.Load) and name in _BUILTINS and name not in bound:
        if name not in ALLOWED_BUILTINS:
            return [("FORBIDDEN_CALL", node, f"builtin {name!r} is not allowed")]
    return []


def _attribute(node: ast.Attribute) -> list[Finding]:
    attr = node.attr
    if attr.startswith("_"):
        return [("PRIVATE_ATTRIBUTE", node, f"attribute {attr!r} starts with an underscore")]
    if attr in INTERNAL_ATTRS:
        return [("PRIVATE_ATTRIBUTE", node, f"attribute {attr!r} is interpreter internals")]
    return []


def _bomb_binop(node: ast.BinOp) -> list[Finding]:
    if isinstance(node.op, ast.Mult):
        for seq, count in ((node.left, node.right), (node.right, node.left)):
            size = _fold(count)
            if _is_sequence(seq) and size is not None and size > MAX_ITEMS:
                return [("RESOURCE_BOMB", node, f"a repeat of {size} items")]
    value = _fold(node)
    if value is not None and abs(value) >= _HUGE:
        return [("RESOURCE_BOMB", node, "an integer past 2**64")]
    return []


def _bomb_range(node: ast.Call) -> list[Finding]:
    if isinstance(node.func, ast.Name) and node.func.id == "range":
        sizes = [_fold(arg) for arg in node.args]
        if any(s is not None and s > MAX_ITEMS for s in sizes):
            return [("RESOURCE_BOMB", node, f"range past {MAX_ITEMS} items")]
    return []


def codes() -> Iterable[str]:
    """The codes this layer can emit, for the finding-code registry."""
    return ("DISALLOWED_SYNTAX", "FORBIDDEN_CALL", "PRIVATE_ATTRIBUTE", "RESOURCE_BOMB")
