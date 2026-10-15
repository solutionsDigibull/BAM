"""E9.2 unit tests: loop_spec compiler and the safe expression evaluator (TD §12.1, §15.2)."""

import ast as python_ast
import builtins
import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import sbam_contracts.gate_expr as gate_expr_module
from fixtures import fixture_path
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from sbam_contracts.isatvon14 import SubSkill
from sbam_workers.isatvon import loop_expr, loop_spec
from sbam_workers.isatvon.loop_expr import (
    EvalContext,
    EvalError,
    ExprError,
    ParseError,
    evaluate,
    parse_expression,
    to_ast,
)
from sbam_workers.isatvon.loop_spec import (
    CompiledLoopSpec,
    LoopSpecError,
    compile_definition,
    compile_loop_spec,
    load_loop_spec,
)

MODULES = (Path(loop_expr.__file__), Path(loop_spec.__file__), Path(gate_expr_module.__file__))

# Golden sha256 of the MS01.1 sample's loop_spec.json. It pins the canonical bytes, so a different
# platform, Python build or dict ordering that changed them would fail here (E9.2-T03).
MS01_1_SHA256 = "sha256:4573dabb70227a82aeb7c252a623def8b90be06b66d79cccada18a373f502445"


def bounds(**changes: Any) -> dict[str, Any]:
    """MS01.1-style bounds (E9.2-T01) with overrides."""
    base: dict[str, Any] = {
        "max_iterations": 3,
        "budget_limit": "2000ms",
        "success_gate": "auth_status == 'VERIFIED'",
        "no_op_condition": "user_status is 'LOCKED'",
        "escalation_condition": "retries >= 3",
        "outputs": ["auth_status"],
        "state": ["retries", "user_status"],
        "inputs": ["mobile_number"],
    }
    return base | changes


def compiled(**changes: Any) -> loop_spec.CompileResult:
    return compile_loop_spec(**bounds(**changes))


def problems_of(**changes: Any) -> list[loop_spec.Diagnostic]:
    with pytest.raises(LoopSpecError) as info:
        compiled(**changes)
    return info.value.problems


def ctx(
    outputs: Mapping[str, object] | None = None,
    state: Mapping[str, object] | None = None,
    inputs: Mapping[str, object] | None = None,
    iteration: int = 1,
    max_iterations: int = 3,
) -> EvalContext:
    return EvalContext(outputs or {}, state or {}, inputs or {}, iteration, max_iterations)


def run(text: str, **kwargs: Any) -> bool:
    return evaluate(to_ast(parse_expression(text)), ctx(**kwargs))


def ms01_1_definition() -> Any:
    raw = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    return SubSkill.model_validate(raw).isatvon_definition


# --- T01 ------------------------------------------------------------------------------------------


def test_e9_2_t01_bounds_compile_and_validate_against_the_json_schema() -> None:
    result = compiled()
    document = json.loads(result.canonical_bytes)
    assert document["schema"] == "sbam-loop-spec/1.0"
    assert document["budget_ms"] == 2000
    assert document["max_iterations"] == 3
    assert document["semantics"] == "TD §15.2"
    assert document["success_gate"]["source"] == "auth_status == 'VERIFIED'"
    assert document["success_gate"]["identifiers"] == ["auth_status"]
    assert document["escalation_condition"]["identifiers"] == ["retries"]
    assert document["no_op_condition"]["identifiers"] == ["user_status"]
    jsonschema.validate(document, CompiledLoopSpec.model_json_schema(by_alias=True))
    assert result.warnings == []


def test_e9_2_t01_ms01_1_sample_compiles_and_evaluates() -> None:
    result = compile_definition(ms01_1_definition())
    spec = result.spec
    assert spec.budget_ms == 5000  # "5000ms timeout per validation cycle"
    assert spec.success_gate.identifiers == ["otp_match", "policy_violation"]
    good = {"otp_match": True, "policy_violation": False}
    assert evaluate(spec.success_gate.ast, ctx(outputs=good)) is True
    bad = {"otp_match": True, "policy_violation": True}
    assert evaluate(spec.success_gate.ast, ctx(outputs=bad)) is False
    locked = ctx(state={"user_status": "LOCKED"})
    assert evaluate(spec.no_op_condition.ast, locked) is True
    escalate = evaluate(spec.escalation_condition.ast, ctx(state={"failed_login_attempts": 3}))
    assert escalate is True
    assert evaluate(spec.escalation_condition.ast, ctx(state={"failed_login_attempts": 2})) is False


def test_e9_2_t01_compiled_document_round_trips_through_the_loader() -> None:
    result = compiled()
    assert load_loop_spec(result.canonical_bytes) == result.spec
    tampered = json.loads(result.canonical_bytes)
    tampered["success_gate"]["ast"] = {"type": "call", "func": "__import__"}
    with pytest.raises(ValidationError):
        load_loop_spec(tampered)
    tampered = json.loads(result.canonical_bytes)
    tampered["success_gate"]["identifiers"] = ["something_else"]
    with pytest.raises(ValidationError):
        load_loop_spec(tampered)


# --- T02 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("auth_status", ["VERIFIED", "PENDING", None])
@pytest.mark.parametrize("retries", [0, 1, 2, 3])
def test_e9_2_t02_evaluation_truth_table(auth_status: str | None, retries: int) -> None:
    spec = compiled().spec
    context = ctx(outputs={"auth_status": auth_status}, state={"retries": retries})
    assert evaluate(spec.success_gate.ast, context) is (auth_status == "VERIFIED")
    assert evaluate(spec.escalation_condition.ast, context) is (retries >= 3)


def test_e9_2_t02_missing_identifier_is_an_error_never_true() -> None:
    spec = compiled().spec
    with pytest.raises(EvalError) as info:
        evaluate(spec.success_gate.ast, ctx(outputs={}))
    assert info.value.code == "EXPR_UNRESOLVED"
    with pytest.raises(EvalError):
        evaluate(parse_ast("NOT missing_flag"), ctx())


def test_e9_2_t02_context_must_be_json_scalars() -> None:
    with pytest.raises(EvalError) as info:
        run("x == 1", outputs={"x": [1]})
    assert info.value.code == "EXPR_TYPE"
    with pytest.raises(EvalError):
        run("x == 1", outputs={"x": {"a": 1}})


def parse_ast(text: str) -> dict[str, Any]:
    return to_ast(parse_expression(text))


# --- T03 ------------------------------------------------------------------------------------------


def test_e9_2_t03_compile_is_byte_identical_and_hash_is_pinned() -> None:
    first, second = compiled(), compiled()
    assert first.canonical_bytes == second.canonical_bytes
    assert first.sha256 == second.sha256
    # Source order of the identifier universe must not change the bytes.
    shuffled = compiled(outputs=["auth_status"], state=["user_status", "retries"])
    assert shuffled.canonical_bytes == first.canonical_bytes
    # Any bound change changes the hash.
    assert compiled(max_iterations=4).sha256 != first.sha256
    assert compiled(budget_limit="2s").sha256 == first.sha256  # same 2000 ms, same bytes


def test_e9_2_t03_ms01_1_golden_hash_is_stable_across_platforms() -> None:
    result = compile_definition(ms01_1_definition())
    assert result.canonical_bytes == result.spec.canonical_bytes()
    assert result.sha256 == MS01_1_SHA256
    assert result.sha256 == "sha256:" + hashlib.sha256(result.canonical_bytes).hexdigest()


# --- T04 ------------------------------------------------------------------------------------------


def _forbidden(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("expression text must never reach Python evaluation")


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('id')",
        "a.__class__",
        "a.__class__.__mro__[1].__subclasses__()",
        "(lambda: 1)()",
        "exec('x')",
        "eval('1')",
        "open('/etc/passwd')",
        "x if y else z",
        "[i for i in range(3)]",
        "a == 1; b",
        "{'a': 1}",
        "a := 1",
        "`id`",
        "$(id)",
        "a ** 2 == 4",
        "x == f'{1}'",
    ],
)
def test_e9_2_t04_injection_is_a_syntax_error_and_python_never_evaluates(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builtins, "eval", _forbidden)
    monkeypatch.setattr(builtins, "exec", _forbidden)
    monkeypatch.setattr(builtins, "compile", _forbidden)
    monkeypatch.setattr(builtins, "__import__", _forbidden)
    with pytest.raises(ParseError) as info:
        parse_expression(text)
    assert info.value.code == "EXPR_SYNTAX"
    found = problems_of(success_gate=text)
    assert [p.code for p in found] == ["EXPR_SYNTAX"]
    assert found[0].path == "v_variables.success_gate"


def test_e9_2_t04_no_module_uses_eval_exec_compile_or_literal_eval() -> None:
    banned_calls = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "getattr",
        "setattr",
        "globals",
        "vars",
    }
    for module in MODULES:
        tree = python_ast.parse(module.read_text(encoding="utf-8"))
        for node in python_ast.walk(tree):
            if isinstance(node, python_ast.Call) and isinstance(node.func, python_ast.Name):
                assert node.func.id not in banned_calls, f"{module.name}: {node.func.id}()"
            if isinstance(node, python_ast.Attribute):
                assert node.attr != "literal_eval", module.name
            if isinstance(node, python_ast.Import):
                assert "ast" not in [alias.name for alias in node.names], module.name
            if isinstance(node, python_ast.ImportFrom):
                assert node.module != "ast", module.name


@pytest.mark.parametrize(
    "node",
    [
        {"type": "call", "func": "__import__", "args": []},
        {"type": "name", "id": "__import__"},
        {"type": "name", "id": "a.b"},
        {"type": "name", "id": "AND"},
        {"type": "name", "id": 1},
        {"type": "name", "id": "x", "extra": 1},
        {"type": "literal", "value": [1]},
        {"type": "literal", "value": {"a": 1}},
        {"type": "literal", "value": float("nan")},
        {"type": "literal", "value": 2**80},
        {"type": "compare", "op": "in", "left": {"type": "name", "id": "a"}, "right": {}},
        {"type": "and", "values": [{"type": "literal", "value": True}]},
        {"type": "not"},
        {"type": "literal"},
        {"type": 5},
        [],
        "a == 1",
        None,
    ],
)
def test_e9_2_t04_hostile_ast_is_rejected_by_the_evaluator(node: object) -> None:
    with pytest.raises(EvalError):
        evaluate(node, ctx())


# --- T05 ------------------------------------------------------------------------------------------


def test_e9_2_t05_unresolved_identifier_names_path_and_identifier() -> None:
    found = problems_of(success_gate="otp_valid == True")
    assert len(found) == 1
    assert found[0].code == "EXPR_UNRESOLVED"
    assert found[0].path == "v_variables.success_gate"
    assert found[0].identifier == "otp_valid"
    assert "otp_valid" in found[0].message


def test_e9_2_t05_every_problem_is_reported_together() -> None:
    found = problems_of(
        success_gate="a_missing == 1",
        escalation_condition="b_missing == 1 AND x(",
        max_iterations=0,
    )
    assert {(p.code, p.path) for p in found} == {
        ("EXPR_UNRESOLVED", "v_variables.success_gate"),
        ("EXPR_SYNTAX", "v_variables.escalation_condition"),
        ("BOUND_INVALID", "v_variables.max_iterations"),
    }


# --- T06 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ms"),
    [
        ("1ms", 1),
        ("500ms", 500),
        ("2s", 2000),
        ("600s", 600_000),
        ("2s per validation cycle", 2000),
    ],
)
def test_e9_2_t06_budget_accepted(text: str, ms: int) -> None:
    assert compiled(budget_limit=text).spec.budget_ms == ms


@pytest.mark.parametrize(
    "text", ["0ms", "0s", "10 minutes", "-5s", "5", "ms", "", " 2s", "2sec", "1e3ms", 5, None]
)
def test_e9_2_t06_budget_rejected_with_422(text: object) -> None:
    with pytest.raises(LoopSpecError) as info:
        compiled(budget_limit=text)
    assert info.value.http_status == 422
    assert [(p.code, p.path) for p in info.value.problems] == [
        ("BOUND_INVALID", "v_variables.budget_limit")
    ]


@pytest.mark.parametrize("value", [0, 11, -1, True, 2.0, "3", None])
def test_e9_2_t06_max_iterations_must_be_an_int_between_1_and_10(value: object) -> None:
    found = problems_of(max_iterations=value)
    assert [(p.code, p.path) for p in found] == [("BOUND_INVALID", "v_variables.max_iterations")]


@pytest.mark.parametrize("value", [1, 10])
def test_e9_2_t06_max_iterations_boundaries_accepted(value: int) -> None:
    assert compiled(max_iterations=value).spec.max_iterations == value


@pytest.mark.parametrize("field", ["success_gate", "no_op_condition", "escalation_condition"])
@pytest.mark.parametrize("value", ["", "   ", None, 5])
def test_e9_2_t06_gates_must_be_non_empty_strings(field: str, value: object) -> None:
    found = problems_of(**{field: value})
    assert [(p.code, p.path) for p in found] == [("BOUND_INVALID", f"v_variables.{field}")]


# --- T07 ------------------------------------------------------------------------------------------


def test_e9_2_t07_depth_and_length_limits_fail_fast() -> None:
    nested = "NOT " * 33 + "a"
    long_text = "x" * 996 + " == 1"
    assert len(long_text) == 1001
    for text in (nested, long_text, "(" * 40 + "a" + ")" * 40):
        started = time.perf_counter()
        with pytest.raises(ParseError) as info:
            parse_expression(text)
        assert time.perf_counter() - started < 0.05
        assert info.value.code == "EXPR_TOO_COMPLEX"
    found = problems_of(success_gate=nested, outputs=["a"])
    assert found[0].code == "EXPR_TOO_COMPLEX"


def test_e9_2_t07_limits_are_inclusive() -> None:
    assert parse_expression("NOT " * 32 + "a")
    assert len("x" * 995 + " == 1") == 1000
    assert parse_expression("x" * 995 + " == 1")
    with pytest.raises(ParseError):
        parse_expression("x" * 996 + " == 1")


def test_e9_2_t07_oversized_ast_is_rejected_without_recursing() -> None:
    node: dict[str, Any] = {"type": "name", "id": "a"}
    for _ in range(100_000):
        node = {"type": "not", "operand": node}
    started = time.perf_counter()
    with pytest.raises(EvalError) as info:
        evaluate(node, ctx())
    assert info.value.code == "EXPR_TOO_COMPLEX"
    assert time.perf_counter() - started < 1


def test_e9_2_t07_self_referencing_ast_terminates() -> None:
    node: dict[str, Any] = {"type": "not"}
    node["operand"] = node
    with pytest.raises(EvalError):
        evaluate(node, ctx())


# --- T08 ------------------------------------------------------------------------------------------

_PIECES = st.sampled_from(
    [
        "a", "b", "retries", "iteration", "pass", "__import__", "os", "AND", "OR", "NOT", "is",
        "not", "True", "False", "None", "true", "false", "null", "==", "!=", "<", "<=", ">", ">=",
        "(", ")", ".", "[", "]", ",", "'s'", '"t"', "'", "1", "-2", "2.5", "99999999999999999999",
        "é", "\x00", " ", "lambda", ":", ";", "eval", "\n",
    ]
)  # fmt: skip
_SCALARS = st.one_of(
    st.none(), st.booleans(), st.integers(-(2**70), 2**70), st.floats(), st.text(max_size=8)
)
_VALUES = st.one_of(_SCALARS, st.lists(_SCALARS, max_size=2), st.dictionaries(st.text(), _SCALARS))
_SCOPE = st.dictionaries(st.sampled_from(["a", "b", "retries", "iteration", "pass"]), _VALUES)


@settings(
    max_examples=10_000,
    deadline=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(
    st.one_of(st.text(max_size=150), st.lists(_PIECES, max_size=40).map(" ".join)),
    _SCOPE,
    _SCOPE,
    _SCOPE,
)
def test_e9_2_t08_fuzz_only_parse_or_eval_errors(
    text: str, outputs: dict[str, object], state: dict[str, object], inputs: dict[str, object]
) -> None:
    try:
        ast_json = to_ast(parse_expression(text))
        json.dumps(ast_json)
    except ParseError:
        return
    try:
        result = evaluate(ast_json, ctx(outputs, state, inputs))
    except EvalError:
        return
    assert isinstance(result, bool)


_JSON = st.recursive(
    st.one_of(_SCALARS, st.just("name"), st.just("literal"), st.just("not")),
    lambda inner: st.one_of(
        st.lists(inner, max_size=3),
        st.dictionaries(
            st.sampled_from(["type", "id", "value", "op", "left", "right", "operand", "values"]),
            inner,
            max_size=4,
        ),
    ),
    max_leaves=20,
)


@settings(max_examples=3000, deadline=100, suppress_health_check=[HealthCheck.too_slow])
@given(_JSON)
def test_e9_2_t08_fuzz_arbitrary_json_as_ast_only_raises_eval_error(node: object) -> None:
    try:
        assert isinstance(evaluate(node, ctx({"id": 1, "value": True})), bool)
    except EvalError:
        pass


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**400, -(10**400)])
def test_e9_2_t08_extreme_context_numbers_raise_eval_error_not_overflow(value: float) -> None:
    for text in ("x > 1", "x == 1", "x <= 1.5"):
        try:
            run(text, outputs={"x": value})
        except EvalError:
            pass


# --- T09 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["true", "True", "1 == 1", "'a' == 'a'", "NOT false", "null is null"]
)
def test_e9_2_t09_constant_true_success_gate_warns_but_compiles(text: str) -> None:
    result = compiled(success_gate=text)
    warning = [w for w in result.warnings if w.code == "EXPR_TRIVIAL"]
    assert [(w.path) for w in warning] == ["v_variables.success_gate"]
    assert result.spec.success_gate.identifiers == []


def test_e9_2_t09_constant_false_and_other_gates_warn_too_but_real_gates_do_not() -> None:
    result = compiled(success_gate="1 == 2", escalation_condition="true")
    assert {w.path for w in result.warnings if w.code == "EXPR_TRIVIAL"} == {
        "v_variables.success_gate",
        "v_variables.escalation_condition",
    }
    assert compiled().warnings == []


def test_e9_2_t09_constant_type_error_is_a_compile_problem_not_a_crash() -> None:
    found = problems_of(success_gate="1 < 'a'")
    assert [p.code for p in found] == ["EXPR_TYPE"]
    assert found[0].path == "v_variables.success_gate"


# --- Edge cases -----------------------------------------------------------------------------------


def test_e9_2_edge_escalation_on_iteration_with_max_iterations_1() -> None:
    result = compiled(max_iterations=1, escalation_condition="iteration >= max_iterations")
    gate = result.spec.escalation_condition
    assert gate.identifiers == ["iteration", "max_iterations"]
    assert evaluate(gate.ast, ctx(iteration=1, max_iterations=1)) is True
    assert evaluate(gate.ast, ctx(iteration=0, max_iterations=1)) is False
    pass_alias = parse_ast("pass >= max_iterations")
    assert evaluate(pass_alias, ctx(iteration=3, max_iterations=3)) is True


def test_e9_2_edge_reserved_names_come_from_the_harness_not_the_scopes() -> None:
    spoofed = ctx(outputs={"iteration": 99}, state={"max_iterations": 99}, iteration=1)
    assert run("iteration == 1", outputs={"iteration": 99}) is True
    assert evaluate(parse_ast("max_iterations == 3"), spoofed) is True
    result = compiled(outputs=["auth_status", "iteration"], escalation_condition="iteration >= 3")
    assert [(w.code, w.identifier) for w in result.warnings] == [("EXPR_SHADOWED", "iteration")]


def test_e9_2_edge_shadowing_resolves_outputs_then_state_then_inputs_with_a_warning() -> None:
    result = compiled(
        success_gate="mode == 'out'",
        outputs=["mode", "auth_status"],
        state=["mode", "retries", "user_status"],
        inputs=["mode"],
    )
    shadow = [w for w in result.warnings if w.code == "EXPR_SHADOWED"]
    assert [(w.identifier, w.path) for w in shadow] == [("mode", "v_variables.success_gate")]
    assert "outputs, state, inputs" in shadow[0].message
    gate = result.spec.success_gate.ast
    assert evaluate(gate, ctx(outputs={"mode": "out"}, state={"mode": "st"}, inputs={"mode": "in"}))
    assert not evaluate(gate, ctx(state={"mode": "st"}, inputs={"mode": "out"}))


@pytest.mark.parametrize(
    "text", ["field is null", "field == null", "field is None", "field == None"]
)
def test_e9_2_edge_null_comparisons(text: str) -> None:
    assert run(text, outputs={"field": None}) is True
    assert run(text, outputs={"field": "x"}) is False
    assert run(text, outputs={"field": 0}) is False


def test_e9_2_edge_is_not_null_and_strings_that_look_like_literals_are_untouched() -> None:
    assert run("field is not null", outputs={"field": "x"}) is True
    assert run("field == 'true'", outputs={"field": "true"}) is True
    assert run('field == "null"', outputs={"field": "null"}) is True
    assert run("field == 'a true b'", outputs={"field": "a true b"}) is True


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("auth.status == 'x'", "EXPR_SYNTAX"),
        ("a.b.c", "EXPR_SYNTAX"),
        ("état == 'x'", "EXPR_SYNTAX"),
        ("名前 == 1", "EXPR_SYNTAX"),
        ("x[0] == 1", "EXPR_SYNTAX"),
        ("f(x)", "EXPR_SYNTAX"),
        ("a == 1.", "EXPR_SYNTAX"),
        ("x == 99999999999999999999", "EXPR_SYNTAX"),
        ("x == 1" + "0" * 400 + ".5", "EXPR_SYNTAX"),
        ("AND", "EXPR_SYNTAX"),
        ("", "EXPR_SYNTAX"),
        ("   ", "EXPR_SYNTAX"),
    ],
)
def test_e9_2_edge_dotted_unicode_and_malformed_identifiers_are_rejected(
    text: str, code: str
) -> None:
    with pytest.raises(ParseError) as info:
        parse_expression(text)
    assert info.value.code == code


def test_e9_2_edge_type_mismatch_raises_instead_of_coercing() -> None:
    with pytest.raises(EvalError) as info:
        run("x < 3", outputs={"x": "10"})
    assert info.value.code == "EXPR_TYPE"
    with pytest.raises(EvalError):
        run("x < 3", outputs={"x": None})
    with pytest.raises(EvalError):
        run("x < 3", outputs={"x": True})
    assert run("x == 1", outputs={"x": True}) is False  # no bool/int coercion
    assert run("x == 1.0", outputs={"x": 1}) is True  # numbers compare as numbers
    with pytest.raises(EvalError):
        run("a AND b", outputs={"a": True, "b": 1})  # non-boolean operand


def test_e9_2_edge_precedence_and_grouping() -> None:
    values = {"a": True, "b": False, "c": True}
    assert run("a OR b AND NOT c", outputs=values) is True
    assert run("(a OR b) AND NOT c", outputs=values) is False
    assert run("NOT NOT a", outputs=values) is True


def test_e9_2_edge_bad_context_is_an_eval_error() -> None:
    ast_json = parse_ast("a")
    for bad in (
        EvalContext({"a": True}, {}, {}, True, 3),
        EvalContext({"a": True}, {}, {}, -1, 3),
        EvalContext({"a": True}, {}, {}, 1, 3.5),  # type: ignore[arg-type]
        EvalContext([], {}, {}, 1, 3),  # type: ignore[arg-type]
    ):
        with pytest.raises(EvalError) as info:
            evaluate(ast_json, bad)
        assert info.value.code == "EXPR_BAD_CONTEXT"
    with pytest.raises(EvalError):
        evaluate(ast_json, None)  # type: ignore[arg-type]


def test_e9_2_edge_every_raised_error_is_an_expr_error() -> None:
    assert issubclass(ParseError, ExprError) and issubclass(EvalError, ExprError)
    assert issubclass(ExprError, ValueError)
