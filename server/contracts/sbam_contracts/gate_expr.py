"""Safe boolean grammar for ISATVON 1.4 loop gates (TD §10.4 rule 5, issue E0.2).

`success_gate`, `no_op_condition` and `escalation_condition` are text written by an LLM or an FDE
and later evaluated by the runtime. They are parsed by this small dedicated grammar and are never
handed to Python `eval`, `exec`, `compile` or the `ast` module.

    expr  := or
    or    := and ("OR" and)*
    and   := not ("AND" not)*
    not   := "NOT" not | cmp
    cmp   := atom (("==" | "!=" | "<" | "<=" | ">" | ">=" | "is" ["not"]) atom)?
    atom  := NUMBER | STRING | True | False | None | IDENT | "(" expr ")"

Rules worth knowing
  * `AND OR NOT is not True False None` are reserved, so a field named `is` or `NOT` cannot be
    referenced; that is a SYNTAX error rather than a silent misparse.
  * Function calls, attribute access and subscripts are rejected with their own error codes.
  * Nesting (NOT and parentheses) is capped at MAX_DEPTH, so deep input fails with TOO_DEEP and
    never reaches Python's recursion limit.
  * `is` is strict equality (`user_status is 'LOCKED'`). Comparisons never coerce: `1 == True` is
    False and `"3" < 4` is a TYPE error.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

MAX_LENGTH: Final = 2000
MAX_DEPTH: Final = 32
KEYWORDS: Final = frozenset({"AND", "OR", "NOT", "is", "not", "True", "False", "None"})
COMPARISON_OPS: Final = frozenset({"==", "!=", "<", "<=", ">", ">=", "is", "is not"})

Value = str | int | float | bool | None


class ErrorCode(StrEnum):
    SYNTAX = "SYNTAX"
    CALL_NOT_ALLOWED = "CALL_NOT_ALLOWED"
    ATTRIBUTE_NOT_ALLOWED = "ATTRIBUTE_NOT_ALLOWED"
    SUBSCRIPT_NOT_ALLOWED = "SUBSCRIPT_NOT_ALLOWED"
    TOO_LONG = "TOO_LONG"
    TOO_DEEP = "TOO_DEEP"
    UNRESOLVED = "UNRESOLVED"
    TYPE = "TYPE"


class ExpressionError(ValueError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# --- AST ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    value: Value


@dataclass(frozen=True)
class Name:
    id: str


@dataclass(frozen=True)
class Compare:
    op: str
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Not:
    operand: "Expr"


@dataclass(frozen=True)
class BoolOp:
    op: str  # "AND" or "OR"
    values: tuple["Expr", ...]


Expr = Literal | Name | Compare | Not | BoolOp


# --- Tokenizer ------------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>-?\d+(?:\.\d+)?(?![\w.]))
  | (?P<string>'[^'\\\n]*'|"[^"\\\n]*")
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|!=|<=|>=|<|>)
  | (?P<paren>[()])
  | (?P<dot>\.)
  | (?P<bracket>[\[\]])
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class _Tok:
    kind: str  # number, string, ident, keyword, op, paren, end
    text: str
    pos: int


def _tokenize(text: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise ExpressionError(ErrorCode.SYNTAX, f"unexpected character {text[pos]!r} at {pos}")
        kind = match.lastgroup or ""
        value = match.group()
        if kind == "dot":
            raise ExpressionError(ErrorCode.ATTRIBUTE_NOT_ALLOWED, f"attribute access at {pos}")
        if kind == "bracket":
            raise ExpressionError(ErrorCode.SUBSCRIPT_NOT_ALLOWED, f"subscript at {pos}")
        if kind == "ident" and value in KEYWORDS:
            kind = "keyword"
        elif kind == "ident":
            if text[match.end() :].lstrip().startswith("("):
                raise ExpressionError(ErrorCode.CALL_NOT_ALLOWED, f"call of {value!r} at {pos}")
            if value.startswith("__"):
                raise ExpressionError(ErrorCode.SYNTAX, f"dunder name {value!r} at {pos}")
        if kind != "ws":
            tokens.append(_Tok(kind, value, pos))
        pos = match.end()
    tokens.append(_Tok("end", "", len(text)))
    return tokens


# --- Parser ---------------------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[_Tok]) -> None:
        self.tokens = tokens
        self.i = 0
        self.depth = 0

    def peek(self) -> _Tok:
        return self.tokens[self.i]

    def take(self) -> _Tok:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def at_keyword(self, word: str) -> bool:
        tok = self.peek()
        return tok.kind == "keyword" and tok.text == word

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError(ErrorCode.TOO_DEEP, f"nesting deeper than {MAX_DEPTH}")

    def parse(self) -> Expr:
        expr = self.parse_or()
        tok = self.peek()
        if tok.kind != "end":
            raise ExpressionError(ErrorCode.SYNTAX, f"unexpected {tok.text!r} at {tok.pos}")
        return expr

    def parse_or(self) -> Expr:
        values = [self.parse_and()]
        while self.at_keyword("OR"):
            self.take()
            values.append(self.parse_and())
        return values[0] if len(values) == 1 else BoolOp("OR", tuple(values))

    def parse_and(self) -> Expr:
        values = [self.parse_not()]
        while self.at_keyword("AND"):
            self.take()
            values.append(self.parse_not())
        return values[0] if len(values) == 1 else BoolOp("AND", tuple(values))

    def parse_not(self) -> Expr:
        if self.at_keyword("NOT"):
            self.take()
            self.enter()
            operand = self.parse_not()
            self.depth -= 1
            return Not(operand)
        return self.parse_cmp()

    def parse_cmp(self) -> Expr:
        left = self.parse_atom()
        tok = self.peek()
        if tok.kind == "op":
            self.take()
            return Compare(tok.text, left, self.parse_atom())
        if self.at_keyword("is"):
            self.take()
            op = "is"
            if self.at_keyword("not"):
                self.take()
                op = "is not"
            return Compare(op, left, self.parse_atom())
        return left

    def parse_atom(self) -> Expr:
        tok = self.take()
        if tok.kind == "number":
            return Literal(float(tok.text) if "." in tok.text else int(tok.text))
        if tok.kind == "string":
            return Literal(tok.text[1:-1])
        if tok.kind == "ident":
            return Name(tok.text)
        if tok.kind == "keyword" and tok.text in ("True", "False", "None"):
            return Literal({"True": True, "False": False, "None": None}[tok.text])
        if tok.kind == "paren" and tok.text == "(":
            self.enter()
            expr = self.parse_or()
            close = self.take()
            if close.text != ")":
                raise ExpressionError(ErrorCode.SYNTAX, f"expected ')' at {close.pos}")
            self.depth -= 1
            return expr
        shown = tok.text or "end of expression"
        raise ExpressionError(ErrorCode.SYNTAX, f"unexpected {shown!r} at {tok.pos}")


def parse(text: str) -> Expr:
    """Parse a gate expression into a safe AST. Raises ExpressionError."""
    if not isinstance(text, str):
        raise ExpressionError(ErrorCode.TYPE, "expression must be a string")
    if len(text) > MAX_LENGTH:
        raise ExpressionError(ErrorCode.TOO_LONG, f"longer than {MAX_LENGTH} characters")
    if not text.strip():
        raise ExpressionError(ErrorCode.SYNTAX, "empty expression")
    return _Parser(_tokenize(text)).parse()


def identifiers(expr: Expr) -> frozenset[str]:
    """Every identifier the expression reads."""
    match expr:
        case Name(id=name):
            return frozenset({name})
        case Literal():
            return frozenset()
        case Compare(left=left, right=right):
            return identifiers(left) | identifiers(right)
        case Not(operand=operand):
            return identifiers(operand)
        case BoolOp(values=values):
            return frozenset().union(*(identifiers(v) for v in values))


# --- Evaluation -----------------------------------------------------------------------------------


def _is_number(value: Value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _strict_equal(a: Value, b: Value) -> bool:
    if _is_number(a) and _is_number(b):
        return a == b
    return type(a) is type(b) and a == b


def _compare(op: str, a: Value, b: Value) -> bool:
    if op in ("==", "is"):
        return _strict_equal(a, b)
    if op in ("!=", "is not"):
        return not _strict_equal(a, b)
    if _is_number(a) and _is_number(b):
        x, y = float(a), float(b)  # type: ignore[arg-type]
    elif isinstance(a, str) and isinstance(b, str):
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    else:
        raise ExpressionError(
            ErrorCode.TYPE, f"cannot order {type(a).__name__} and {type(b).__name__}"
        )
    return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


def _bool(value: Value, what: str) -> bool:
    if not isinstance(value, bool):
        raise ExpressionError(ErrorCode.TYPE, f"{what} is {type(value).__name__}, not a boolean")
    return value


def evaluate(
    expr: Expr,
    inputs: Mapping[str, object],
    state: Mapping[str, object],
    outputs: Mapping[str, object],
) -> bool:
    """Evaluate a parsed gate. Names resolve from outputs, then state, then inputs."""

    def lookup(name: str) -> Value:
        for scope in (outputs, state, inputs):
            if name in scope:
                value = scope[name]
                if value is None or isinstance(value, str | int | float | bool):
                    return value
                raise ExpressionError(ErrorCode.TYPE, f"{name!r} is {type(value).__name__}")
        raise ExpressionError(ErrorCode.UNRESOLVED, f"unresolved identifier {name!r}")

    def value_of(node: Expr) -> Value:
        match node:
            case Literal(value=value):
                return value
            case Name(id=name):
                return lookup(name)
            case Compare(op=op, left=left, right=right):
                return _compare(op, value_of(left), value_of(right))
            case Not(operand=operand):
                return not _bool(value_of(operand), "NOT operand")
            case BoolOp(op="AND", values=values):
                return all(_bool(value_of(v), "AND operand") for v in values)
            case BoolOp(values=values):
                return any(_bool(value_of(v), "OR operand") for v in values)

    return _bool(value_of(expr), "expression")
