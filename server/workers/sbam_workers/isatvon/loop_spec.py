"""`loop_spec.json` compiler (TD §12.1, §15.2, issue E9.2).

Compiles the five ISATVON 1.4 loop bounds of one Sub-Skill into an evaluable, byte-stable document:

    {"schema": "sbam-loop-spec/1.0", "max_iterations", "budget_ms",
     "success_gate" | "no_op_condition" | "escalation_condition": {source, ast, identifiers},
     "semantics": "TD §15.2"}

Compile-time checks (all problems are collected, none is fixed up silently)
  * max_iterations is an int 1-10; budget_limit is `<n>ms|s` plus optional text and above 0 ms;
  * each gate is non-empty, parses in the safe grammar and fits the length/depth limits;
  * every identifier resolves to the Sub-Skill's outputs, fresh state, required inputs or a reserved
    name (`iteration`, `pass`, `max_iterations`), else EXPR_UNRESOLVED at `v_variables.<gate>`.

Warnings (compile still succeeds, the FDE sees them in the validation report)
  * EXPR_TRIVIAL: a gate that reads no identifier and is constant (`true`, `1 == 1`), R16;
  * EXPR_SHADOWED: an identifier present in several sources, or a reserved name that is also a
    declared field. Outputs beat state beat inputs, reserved names beat all three.

The bytes are RFC 8785 canonical JSON, so the same bounds give the same bytes and sha256 on every
platform. The evaluator that runs the AST is `sbam_workers.isatvon.loop_expr`.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from sbam_contracts.canonical import canonical_json, sha256_of
from sbam_contracts.gate_expr import Expr
from sbam_contracts.gate_expr import identifiers as expr_identifiers
from sbam_contracts.isatvon14 import (
    MAX_ITERATIONS_RANGE,
    IsatvonDefinition,
    output_fields,
    parse_budget_ms,
)

from sbam_workers.isatvon import loop_expr
from sbam_workers.isatvon.loop_expr import ExprError, ParseError

SCHEMA_ID: Final = "sbam-loop-spec/1.0"
SEMANTICS_REF: Final = "TD §15.2"
GATE_FIELDS: Final = ("success_gate", "no_op_condition", "escalation_condition")
PATH_PREFIX: Final = "v_variables."
MAX_GATE_LENGTH: Final = loop_expr.MAX_EXPRESSION_LENGTH
BUDGET_TEXT: Final = re.compile(r"^(\d+)(ms|s)( .*)?$", re.DOTALL)

BOUND_INVALID: Final = "BOUND_INVALID"
EXPR_TRIVIAL: Final = "EXPR_TRIVIAL"
EXPR_SHADOWED: Final = "EXPR_SHADOWED"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    path: str
    message: str
    identifier: str | None = None


class LoopSpecError(ValueError):
    """The bounds cannot be compiled. Map to HTTP 422; `problems` lists every reason."""

    http_status: Final = 422

    def __init__(self, problems: list[Diagnostic]) -> None:
        super().__init__("; ".join(f"{p.code} at {p.path}: {p.message}" for p in problems))
        self.problems = problems


class CompiledGate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source: str = Field(min_length=1, max_length=MAX_GATE_LENGTH)
    ast: dict[str, JsonValue]
    identifiers: list[str]

    @model_validator(mode="after")
    def _ast_is_safe(self) -> "CompiledGate":
        try:
            names = sorted(loop_expr.identifiers_of(self.ast))
        except ExprError as exc:
            raise ValueError(str(exc)) from None
        if self.identifiers != names:
            raise ValueError(f"identifiers {self.identifiers} do not match the AST {names}")
        return self


class CompiledLoopSpec(BaseModel):
    """The `loop_spec.json` document. Loading it re-validates every AST."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)

    schema_id: Literal["sbam-loop-spec/1.0"] = Field(alias="schema")
    max_iterations: int = Field(ge=MAX_ITERATIONS_RANGE[0], le=MAX_ITERATIONS_RANGE[1])
    budget_ms: int = Field(gt=0)
    success_gate: CompiledGate
    no_op_condition: CompiledGate
    escalation_condition: CompiledGate
    semantics: Literal["TD §15.2"]

    def to_json_object(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def canonical_bytes(self) -> bytes:
        """RFC 8785 bytes; these are what the bundle stores and hashes."""
        return canonical_json(self.to_json_object())

    def sha256(self) -> str:
        return sha256_of(self.to_json_object())


@dataclass(frozen=True)
class CompileResult:
    spec: CompiledLoopSpec
    canonical_bytes: bytes
    sha256: str  # "sha256:" + 64 hex, the value stored in the bundle file hashes
    warnings: list[Diagnostic]


def _budget_ms(text: object) -> int:
    if not isinstance(text, str) or BUDGET_TEXT.match(text) is None:
        raise ValueError(f"budget_limit {text!r} is not '<n>ms' or '<n>s' (optional text after)")
    return parse_budget_ms(text)


def _compile_gate(
    field: str, text: object, known: frozenset[str], problems: list[Diagnostic]
) -> tuple[CompiledGate, Expr] | None:
    path = PATH_PREFIX + field
    if not isinstance(text, str) or not text.strip():
        problems.append(Diagnostic(BOUND_INVALID, path, "must be a non-empty string"))
        return None
    try:
        expr = loop_expr.parse_expression(text)
    except ParseError as exc:
        problems.append(Diagnostic(exc.code, path, exc.message))
        return None
    names = sorted(expr_identifiers(expr))
    unresolved = [n for n in names if n not in known]
    for name in unresolved:
        problems.append(
            Diagnostic(loop_expr.EXPR_UNRESOLVED, path, f"unresolved identifier {name!r}", name)
        )
    if unresolved:
        return None
    gate = CompiledGate(source=text, ast=loop_expr.to_ast(expr), identifiers=names)
    return gate, expr


def compile_loop_spec(
    *,
    max_iterations: object,
    budget_limit: object,
    success_gate: object,
    no_op_condition: object,
    escalation_condition: object,
    outputs: Iterable[str],
    state: Iterable[str],
    inputs: Iterable[str],
) -> CompileResult:
    """Compile the five bounds. Raises LoopSpecError (422) listing every problem found."""
    out, st, inp = frozenset(outputs), frozenset(state), frozenset(inputs)
    known = out | st | inp | loop_expr.RESERVED_NAMES
    problems: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    low, high = MAX_ITERATIONS_RANGE
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or not low <= max_iterations <= high
    ):
        problems.append(
            Diagnostic(
                BOUND_INVALID, PATH_PREFIX + "max_iterations", f"must be an integer {low}-{high}"
            )
        )
    budget_ms = 0
    try:
        budget_ms = _budget_ms(budget_limit)
    except ValueError as exc:
        problems.append(Diagnostic(BOUND_INVALID, PATH_PREFIX + "budget_limit", str(exc)))

    gates = dict(
        zip(GATE_FIELDS, (success_gate, no_op_condition, escalation_condition), strict=True)
    )
    compiled: dict[str, CompiledGate] = {}
    for field, text in gates.items():
        result = _compile_gate(field, text, known, problems)
        if result is None:
            continue
        gate, expr = result
        compiled[field] = gate
        path = PATH_PREFIX + field
        try:
            constant = loop_expr.constant_value(expr)
        except ExprError as exc:
            problems.append(Diagnostic(exc.code, path, exc.message))
            continue
        if constant is not None:
            warnings.append(
                Diagnostic(
                    EXPR_TRIVIAL,
                    path,
                    f"always {constant}: it reads no field, so it can never change between passes",
                )
            )
        for name in gate.identifiers:
            warning = _shadow_warning(name, path, out, st, inp)
            if warning is not None:
                warnings.append(warning)
    if problems:
        raise LoopSpecError(problems)

    spec = CompiledLoopSpec.model_validate(
        {
            "schema": SCHEMA_ID,
            "max_iterations": max_iterations,
            "budget_ms": budget_ms,
            **{field: compiled[field].model_dump() for field in GATE_FIELDS},
            "semantics": SEMANTICS_REF,
        }
    )
    return CompileResult(spec, spec.canonical_bytes(), spec.sha256(), warnings)


def _shadow_warning(
    name: str, path: str, out: frozenset[str], st: frozenset[str], inp: frozenset[str]
) -> Diagnostic | None:
    if name in loop_expr.RESERVED_NAMES:
        if name in out | st | inp:
            return Diagnostic(
                EXPR_SHADOWED,
                path,
                f"{name!r} is reserved and always comes from the loop harness; "
                "the declared field of that name is ignored",
                name,
            )
        return None
    sources = [
        label
        for label, scope in (("outputs", out), ("state", st), ("inputs", inp))
        if name in scope
    ]
    if len(sources) > 1:
        return Diagnostic(
            EXPR_SHADOWED,
            path,
            f"{name!r} exists in {', '.join(sources)}; it resolves to {sources[0]} "
            "(precedence outputs > state > inputs)",
            name,
        )
    return None


def compile_definition(definition: IsatvonDefinition) -> CompileResult:
    """Compile from a structured `isatvon_definition` (V, plus the S and O identifier universe)."""
    v = definition.v_variables
    return compile_loop_spec(
        max_iterations=v.max_iterations,
        budget_limit=v.budget_limit,
        success_gate=v.success_gate,
        no_op_condition=v.no_op_condition,
        escalation_condition=v.escalation_condition,
        outputs=output_fields(definition.o_outcome.json_schema),
        state=definition.s_source.fresh_state_parameters,
        inputs=definition.s_source.required_inputs,
    )


def load_loop_spec(data: bytes | str | Mapping[str, Any]) -> CompiledLoopSpec:
    """Load a `loop_spec.json` for the harness. Raises ValidationError for a tampered document."""
    if isinstance(data, Mapping):
        return CompiledLoopSpec.model_validate(data)
    return CompiledLoopSpec.model_validate_json(data)
