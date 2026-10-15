"""Safe loop-gate expressions: text to JSON AST, and JSON AST evaluation (E9.2, TD §12.1, §15.2).

`success_gate`, `no_op_condition` and `escalation_condition` are compiled into `loop_spec.json` as
`{source, ast, identifiers}`. The sandbox loop harness (and SRIM) load that AST and call `evaluate`.
This module is pure: no I/O, no clock, no randomness, and no Python `eval`, `exec`, `compile`,
`ast.literal_eval`, `getattr` or `__import__` anywhere (a unit test scans the source to keep it so).

Grammar: the one in `sbam_contracts.gate_expr` (comparisons, AND/OR/NOT, `is`, literals and plain
identifiers; no calls, attributes, subscripts or lambdas). On top of it this module adds
  * lowercase literal aliases `true`, `false`, `null` for `True`, `False`, `None`;
  * a 1000 character limit (stricter than the contract's 2000) and the grammar's nesting limit,
    both reported as EXPR_TOO_COMPLEX;
  * a JSON AST, validated strictly when loaded back, so a tampered `loop_spec.json` cannot reach
    the evaluator in a shape the parser would never produce.

Errors: `ParseError` (compile time) and `EvalError` (run time), both `ExprError` with a stable
`code`. Nothing else is raised, whatever the input.
"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from sbam_contracts import gate_expr
from sbam_contracts.gate_expr import Compare, Expr, Literal, Name, Not, Value

MAX_EXPRESSION_LENGTH: Final = 1000
MAX_AST_DEPTH: Final = 70  # node levels; the parser's 32 NOT/paren nestings stay well inside it
MAX_AST_NODES: Final = 1000
MAX_SAFE_INT: Final = 2**53 - 1  # I-JSON range, so the canonical (RFC 8785) bytes always exist
RESERVED_ITERATION: Final = ("iteration", "pass")  # both are the current pass number
RESERVED_NAMES: Final = frozenset({*RESERVED_ITERATION, "max_iterations"})

EXPR_SYNTAX: Final = "EXPR_SYNTAX"
EXPR_TOO_COMPLEX: Final = "EXPR_TOO_COMPLEX"
EXPR_UNRESOLVED: Final = "EXPR_UNRESOLVED"
EXPR_TYPE: Final = "EXPR_TYPE"
EXPR_BAD_AST: Final = "EXPR_BAD_AST"
EXPR_BAD_CONTEXT: Final = "EXPR_BAD_CONTEXT"

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LITERAL_ALIASES = {"true": "True", "false": "False", "null": "None"}
_ALIAS = re.compile(r"""('[^'\\\n]*'|"[^"\\\n]*")|(?<![\w.])(true|false|null)(?![\w.])""")
_GATE_CODES = {
    gate_expr.ErrorCode.TOO_LONG: EXPR_TOO_COMPLEX,
    gate_expr.ErrorCode.TOO_DEEP: EXPR_TOO_COMPLEX,
    gate_expr.ErrorCode.UNRESOLVED: EXPR_UNRESOLVED,
    gate_expr.ErrorCode.TYPE: EXPR_TYPE,
}


class ExprError(ValueError):
    """Base of every error this module raises. `code` is stable and machine readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ParseError(ExprError):
    """The text or AST is not a valid gate expression."""


class EvalError(ExprError):
    """The expression cannot be evaluated against this context (never silently False)."""


# --- Text -> AST ----------------------------------------------------------------------------------


def _normalise_aliases(text: str) -> str:
    def swap(match: re.Match[str]) -> str:
        return match.group(1) or _LITERAL_ALIASES[match.group(2)]

    return _ALIAS.sub(swap, text)


def parse_expression(text: str) -> Expr:
    """Parse gate text with the SBAM grammar. Raises ParseError only."""
    if not isinstance(text, str):
        raise ParseError(EXPR_SYNTAX, "expression must be a string")
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise ParseError(EXPR_TOO_COMPLEX, f"longer than {MAX_EXPRESSION_LENGTH} characters")
    try:
        expr = gate_expr.parse(_normalise_aliases(text))
        _check_literals(expr)
    except gate_expr.ExpressionError as exc:
        code = _GATE_CODES.get(exc.code, EXPR_SYNTAX)
        raise ParseError(code, f"{exc.code.value}: {exc.message}") from None
    except RecursionError:  # unreachable behind the depth limit; kept so nothing else can escape
        raise ParseError(EXPR_TOO_COMPLEX, "nesting too deep") from None
    return expr


def _check_literals(expr: Expr) -> None:
    """Numbers must be finite and inside the I-JSON range so the AST has canonical bytes."""
    stack: list[Expr] = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, Literal):
            _scalar_ok(node.value, gate_expr.ExpressionError)
        elif isinstance(node, Compare):
            stack += [node.left, node.right]
        elif isinstance(node, Not):
            stack.append(node.operand)
        elif isinstance(node, gate_expr.BoolOp):
            stack += node.values


def _scalar_ok(value: object, error: Any) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int) and abs(value) <= MAX_SAFE_INT:
        return
    if isinstance(value, float) and math.isfinite(value) and abs(value) <= MAX_SAFE_INT:
        return
    if error is gate_expr.ExpressionError:
        raise gate_expr.ExpressionError(gate_expr.ErrorCode.SYNTAX, "number out of range")
    raise error(EXPR_BAD_AST, "literal is not a JSON scalar in range")


def to_ast(expr: Expr) -> dict[str, Any]:
    """The JSON AST stored in `loop_spec.json`."""
    match expr:
        case Literal(value=value):
            return {"type": "literal", "value": value}
        case Name(id=name):
            return {"type": "name", "id": name}
        case Compare(op=op, left=left, right=right):
            return {"type": "compare", "op": op, "left": to_ast(left), "right": to_ast(right)}
        case Not(operand=operand):
            return {"type": "not", "operand": to_ast(operand)}
        case gate_expr.BoolOp(op=op, values=values):
            return {"type": op.lower(), "values": [to_ast(v) for v in values]}


# --- AST -> Expr (strict) -------------------------------------------------------------------------


def _bad(message: str) -> ParseError:
    return ParseError(EXPR_BAD_AST, message)


def _exact_keys(node: Mapping[str, object], keys: set[str], kind: str) -> None:
    if set(node) != keys:
        raise _bad(f"{kind} node needs exactly the keys {sorted(keys)}")


def _measure(ast: object) -> None:
    """Iterative size/depth check, so a hostile AST cannot exhaust the Python stack."""
    stack: list[tuple[object, int]] = [(ast, 1)]
    seen = 0
    while stack:
        node, depth = stack.pop()
        seen += 1
        if seen > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise ParseError(EXPR_TOO_COMPLEX, "AST is too large or too deep")
        if not isinstance(node, dict):
            continue  # reported by from_ast with a precise message
        for key in ("left", "right", "operand"):
            if key in node:
                stack.append((node[key], depth + 1))
        values = node.get("values")
        if isinstance(values, list):
            stack.extend((child, depth + 1) for child in values)


def from_ast(ast: object) -> Expr:
    """Rebuild a safe expression from a JSON AST. Raises ParseError for anything unexpected."""
    _measure(ast)
    return _build(ast)


def _build(node: object) -> Expr:
    if not isinstance(node, dict) or not all(isinstance(k, str) for k in node):
        raise _bad("AST node must be an object")
    kind = node.get("type")
    if kind == "literal":
        _exact_keys(node, {"type", "value"}, "literal")
        _scalar_ok(node["value"], ParseError)
        return Literal(node["value"])
    if kind == "name":
        _exact_keys(node, {"type", "id"}, "name")
        ident = node["id"]
        if (
            not isinstance(ident, str)
            or _IDENT.fullmatch(ident) is None
            or ident in gate_expr.KEYWORDS
            or ident.startswith("__")
        ):
            raise _bad("name node has an invalid identifier")
        return Name(ident)
    if kind == "compare":
        _exact_keys(node, {"type", "op", "left", "right"}, "compare")
        op = node["op"]
        if not isinstance(op, str) or op not in gate_expr.COMPARISON_OPS:
            raise _bad("compare node has an unknown operator")
        return Compare(op, _build(node["left"]), _build(node["right"]))
    if kind == "not":
        _exact_keys(node, {"type", "operand"}, "not")
        return Not(_build(node["operand"]))
    if kind in ("and", "or"):
        _exact_keys(node, {"type", "values"}, str(kind))
        values = node["values"]
        if not isinstance(values, list) or len(values) < 2:
            raise _bad("and/or node needs a list of at least 2 values")
        return gate_expr.BoolOp(str(kind).upper(), tuple(_build(v) for v in values))
    raise _bad("unknown AST node type")


def identifiers_of(ast: object) -> frozenset[str]:
    """Every identifier a JSON AST reads (validates the AST first)."""
    return gate_expr.identifiers(from_ast(ast))


# --- Evaluation -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalContext:
    """What the harness hands the evaluator for one pass.

    Names resolve outputs > state > inputs. `iteration` / `pass` (the current pass number) and
    `max_iterations` are reserved and always come from the harness, never from the three scopes.
    Only JSON scalars (str, int, float, bool, null) can be read; anything else is an EvalError.
    """

    outputs: Mapping[str, object]
    state: Mapping[str, object]
    inputs: Mapping[str, object]
    iteration: int
    max_iterations: int


def _count(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SAFE_INT:
        raise EvalError(EXPR_BAD_CONTEXT, f"{what} must be an integer >= 0")
    return value


def evaluate(ast: object, ctx: EvalContext) -> bool:
    """Evaluate a JSON AST against one pass. Returns a bool or raises EvalError, nothing else."""
    try:
        expr = from_ast(ast)
    except ParseError as exc:
        raise EvalError(exc.code, exc.message) from None
    if not isinstance(ctx, EvalContext):
        raise EvalError(EXPR_BAD_CONTEXT, "ctx must be an EvalContext")
    for scope in (ctx.outputs, ctx.state, ctx.inputs):
        if not isinstance(scope, Mapping):
            raise EvalError(EXPR_BAD_CONTEXT, "outputs, state and inputs must be mappings")
    iteration = _count(ctx.iteration, "iteration")
    limit = _count(ctx.max_iterations, "max_iterations")
    reserved: dict[str, object] = {"max_iterations": limit}
    reserved |= dict.fromkeys(RESERVED_ITERATION, iteration)
    try:
        return gate_expr.evaluate(expr, ctx.inputs, ctx.state, {**ctx.outputs, **reserved})
    except gate_expr.ExpressionError as exc:
        raise EvalError(_GATE_CODES.get(exc.code, EXPR_TYPE), exc.message) from None
    except (ArithmeticError, RecursionError):
        raise EvalError(EXPR_TYPE, "number out of range") from None


def constant_value(expr: Expr) -> bool | None:
    """True/False when the expression reads no identifier, None when it does.

    Raises EvalError for a constant that cannot be evaluated (`1 < 'a'`).
    """
    if gate_expr.identifiers(expr):
        return None
    try:
        return gate_expr.evaluate(expr, {}, {}, {})
    except gate_expr.ExpressionError as exc:
        raise EvalError(_GATE_CODES.get(exc.code, EXPR_TYPE), exc.message) from None


def scalar(value: object) -> Value:
    """`value` if it is a JSON scalar; EvalError otherwise (for harness-side checks)."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise EvalError(EXPR_TYPE, f"{type(value).__name__} is not a JSON scalar")
