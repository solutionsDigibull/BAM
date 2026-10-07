"""E0.2 unit tests for the safe gate-expression grammar (TD §10.4 rule 5)."""

import builtins

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sbam_contracts.gate_expr import (
    MAX_DEPTH,
    MAX_LENGTH,
    BoolOp,
    Compare,
    ErrorCode,
    ExpressionError,
    Literal,
    Name,
    Not,
    evaluate,
    identifiers,
    parse,
)


def code_of(text: str) -> ErrorCode:
    with pytest.raises(ExpressionError) as info:
        parse(text)
    return info.value.code


# --- Parsing --------------------------------------------------------------------------------------


def test_e0_2_parses_the_isa14_sample_gates() -> None:
    assert parse("otp_match == True AND policy_violation == False") == BoolOp(
        "AND",
        (
            Compare("==", Name("otp_match"), Literal(True)),
            Compare("==", Name("policy_violation"), Literal(False)),
        ),
    )
    assert parse("user_status is 'LOCKED'") == Compare("is", Name("user_status"), Literal("LOCKED"))
    assert identifiers(parse("failed_login_attempts >= max_iterations")) == {
        "failed_login_attempts",
        "max_iterations",
    }


def test_e0_2_precedence_not_binds_tighter_than_and_than_or() -> None:
    assert parse("NOT a AND b OR c") == BoolOp(
        "OR", (BoolOp("AND", (Not(Name("a")), Name("b"))), Name("c"))
    )
    assert parse("x is not None") == Compare("is not", Name("x"), Literal(None))
    assert parse("-1.5 < x") == Compare("<", Literal(-1.5), Name("x"))


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("__import__('os').system('id')", ErrorCode.CALL_NOT_ALLOWED),
        ("a.b == 1", ErrorCode.ATTRIBUTE_NOT_ALLOWED),
        ("x[0] == 1", ErrorCode.SUBSCRIPT_NOT_ALLOWED),
        ("f(x)", ErrorCode.CALL_NOT_ALLOWED),
        ("f (x)", ErrorCode.CALL_NOT_ALLOWED),
        ("lambda: 1", ErrorCode.SYNTAX),
        ("__class__ == 1", ErrorCode.SYNTAX),
        ("a == 1; b", ErrorCode.SYNTAX),
    ],
)
def test_e0_2_t06_expression_injection_is_rejected(text: str, code: ErrorCode) -> None:
    assert code_of(text) == code


@pytest.mark.parametrize("text", ["is == 1", "NOT == 1", "x == AND", "True(1)", "a ==", "", "  "])
def test_e0_2_keyword_collisions_and_broken_input_are_syntax_errors(text: str) -> None:
    assert code_of(text) == ErrorCode.SYNTAX


def test_e0_2_t10_deep_nesting_hits_the_depth_limit_not_recursion() -> None:
    assert code_of("NOT " * 300 + "a") == ErrorCode.TOO_DEEP
    assert code_of("NOT " * 200 + "a") == ErrorCode.TOO_DEEP
    assert code_of("(" * 300 + "a" + ")" * 300) == ErrorCode.TOO_DEEP
    assert parse("NOT " * MAX_DEPTH + "a")  # exactly at the limit is fine


def test_e0_2_overlong_expression_is_rejected() -> None:
    assert code_of("a == 1 AND " * 300 + "a == 1") == ErrorCode.TOO_LONG
    assert len("a == 1 AND " * 300) > MAX_LENGTH


# --- Evaluation -----------------------------------------------------------------------------------


def test_e0_2_t10_escalation_condition_evaluates_true() -> None:
    gate = parse("retries >= max_iterations")
    assert evaluate(gate, {}, {"retries": 3, "max_iterations": 3}, {}) is True
    assert evaluate(gate, {}, {"retries": 2, "max_iterations": 3}, {}) is False


def test_e0_2_names_resolve_outputs_then_state_then_inputs() -> None:
    gate = parse("x == 'out'")
    assert evaluate(gate, {"x": "in"}, {"x": "state"}, {"x": "out"}) is True
    assert evaluate(parse("x == 'in'"), {"x": "in"}, {}, {}) is True


def test_e0_2_comparisons_never_coerce() -> None:
    assert evaluate(parse("flag == 1"), {}, {}, {"flag": True}) is False
    assert evaluate(parse("n == 1.0"), {}, {}, {"n": 1}) is True
    assert evaluate(parse("s is not 'LOCKED'"), {}, {}, {"s": "OPEN"}) is True
    with pytest.raises(ExpressionError) as info:
        evaluate(parse("s < 4"), {}, {}, {"s": "3"})
    assert info.value.code == ErrorCode.TYPE


@pytest.mark.parametrize(
    ("text", "outputs"),
    [("a AND b", {"a": True, "b": 1}), ("x", {"x": "yes"}), ("o > 1", {"o": [1]})],
)
def test_e0_2_non_boolean_operands_are_type_errors(text: str, outputs: dict[str, object]) -> None:
    with pytest.raises(ExpressionError) as info:
        evaluate(parse(text), {}, {}, outputs)
    assert info.value.code == ErrorCode.TYPE


def test_e0_2_unknown_identifier_at_evaluation_is_unresolved() -> None:
    with pytest.raises(ExpressionError) as info:
        evaluate(parse("unknown_x > 1"), {}, {}, {})
    assert info.value.code == ErrorCode.UNRESOLVED


# --- Fuzzing: arbitrary text is parsed or rejected, never executed --------------------------------

_PIECES = st.sampled_from(
    [
        "a", "b_1", "x", "__import__", "os", "AND", "OR", "NOT", "is", "not", "True", "None",
        "==", "!=", "<", ">=", "(", ")", ".", "[", "]", ",", "'s'", '"t"', "1", "-2.5", " ",
        "lambda", ":", "system", "eval",
    ]
)  # fmt: skip


def _forbidden(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("gate_expr must never execute Python")


@settings(max_examples=400, deadline=None)
@given(st.one_of(st.text(max_size=120), st.lists(_PIECES, max_size=40).map("".join)))
def test_e0_2_t06_fuzz_never_executes_python(text: str) -> None:
    saved = builtins.eval, builtins.exec, builtins.compile
    builtins.eval, builtins.exec, builtins.compile = _forbidden, _forbidden, _forbidden  # type: ignore[assignment]
    try:
        try:
            tree = parse(text)
        except ExpressionError:
            return
        values = dict.fromkeys(identifiers(tree), True)
        try:
            evaluate(tree, {}, {}, values)
        except ExpressionError:
            pass
    finally:
        builtins.eval, builtins.exec, builtins.compile = saved
