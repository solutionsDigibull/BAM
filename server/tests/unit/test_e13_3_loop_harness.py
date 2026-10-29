"""E13.3 unit tests: the bounded-loop harness on MS01.1 with fake tools and state (TD §15.2)."""

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any

import pytest
from fixtures import fixture_path
from pydantic import JsonValue, ValidationError
from sbam_contracts.harness import HarnessTrace
from sbam_contracts.isatvon14 import IsatvonDefinition, SubSkill, output_fields
from sbam_workers.isatvon.loop_spec import (
    BOUND_INVALID,
    CompiledLoopSpec,
    LoopSpecError,
    compile_loop_spec,
    load_loop_spec,
)
from sbam_workers.test.harness import (
    HandoffSpec,
    LoopHarnessError,
    LoopTestCase,
    progress_message,
    run_case,
)

ACTION, CHECK = "verify_otp", "check_auth_policy"
COMPLETION = "Status: Verifying -> Looping [Pass 2/3]: Incorrect OTP -> Authenticated"
ESCALATION = "Status: Escalated - Account Locked"
INPUTS: dict[str, JsonValue] = {
    "mobile_number": "+919800000001",
    "otp_payload": "123456",
    "device_fingerprint": "fp-test-1",
}


def ms01_1() -> IsatvonDefinition:
    raw = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    return SubSkill.model_validate(raw).isatvon_definition


def spec_for(definition: IsatvonDefinition, **overrides: object) -> CompiledLoopSpec:
    v = definition.v_variables
    bounds: dict[str, object] = {
        "max_iterations": v.max_iterations,
        "budget_limit": v.budget_limit,
        "success_gate": v.success_gate,
        "no_op_condition": v.no_op_condition,
        "escalation_condition": v.escalation_condition,
    }
    bounds.update(overrides)
    result = compile_loop_spec(
        **bounds,
        outputs=output_fields(definition.o_outcome.json_schema),
        state=definition.s_source.fresh_state_parameters,
        inputs=definition.s_source.required_inputs,
    )
    # The harness reads the published document, not the in-memory compile result.
    return load_loop_spec(result.canonical_bytes)


class FakeStore:
    def __init__(self, **state: Any) -> None:
        self.state: dict[str, Any] = {
            "failed_login_attempts": 0,
            "active_devices": 1,
            "user_status": "ACTIVE",
            **state,
        }
        self.reads = 0
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def read_state(self, names: list[str]) -> dict[str, Any]:
        self.reads += 1
        return {name: self.state[name] for name in names}

    def apply_script(self, changes: Mapping[str, JsonValue]) -> None:
        self.state.update(changes)

    def append_handoff(self, table: str, row: Mapping[str, Any]) -> None:
        self.rows.append((table, dict(row)))


class FakeTools:
    """`check(pass_no, arguments)` decides the check output; the action echoes an access scope."""

    def __init__(self, check: Any = None, action_sleep: float = 0.0, raises: bool = False) -> None:
        self.calls: list[str] = []
        self.check = check or (lambda pass_no, args: {"otp_match": False})
        self.action_sleep = action_sleep
        self.raises = raises

    async def call(self, tool: str, arguments: Mapping[str, JsonValue]) -> dict[str, Any]:
        self.calls.append(tool)
        pass_no = arguments["iteration_pass"]
        if tool == ACTION:
            if self.raises:
                raise RuntimeError("tool crashed")
            if self.action_sleep:
                await asyncio.sleep(self.action_sleep)
            return {"auth_status": "REJECTED", "access_scope": "none"}
        return {"otp_match": False, "policy_violation": False, **self.check(pass_no, arguments)}

    def count(self, tool: str) -> int:
        return self.calls.count(tool)


def case(kind: str, expected: str, **extra: Any) -> LoopTestCase:
    return LoopTestCase.model_validate(
        {
            "case_id": f"MS01.1-{kind}",
            "kind": kind,
            "expected_terminal": expected,
            "inputs": INPUTS,
            "action_tool": ACTION,
            "fixed_check_tool": CHECK,
            **extra,
        }
    )


def run(
    spec: CompiledLoopSpec,
    test_case: LoopTestCase,
    tools: FakeTools,
    store: FakeStore,
    definition: IsatvonDefinition | None = None,
) -> HarnessTrace:
    d = definition or ms01_1()
    return asyncio.run(
        run_case(
            spec,
            test_case,
            fresh_state_parameters=d.s_source.fresh_state_parameters,
            handoff=HandoffSpec.model_validate(
                d.o_outcome.state_handoff_file.model_dump(include={"table", "required_fields"})
            ),
            notification=d.n_notification,
            tools=tools,
            store=store,
            required_inputs=d.s_source.required_inputs,
        )
    )


def assert_consistent(trace: HarnessTrace, max_iterations: int) -> None:
    """The trace output checks from the issue: bound, numbering, progress count, gate agreement."""
    assert trace.iterations == len(trace.passes) <= max_iterations
    assert [p.pass_no for p in trace.passes] == list(range(1, trace.iterations + 1))
    progress = [u for u in trace.ui_updates if u.startswith("Looping [Pass ")]
    assert progress == [progress_message(p.pass_no, max_iterations) for p in trace.passes]
    if trace.terminal == "SUCCESS":
        assert trace.passes[-1].gate_eval.success
    if trace.terminal == "ESCALATED":
        assert not any(p.gate_eval.success for p in trace.passes)


def test_e13_3_t01_pass_first_on_ms01_1() -> None:
    d = ms01_1()
    spec = spec_for(d)
    store, tools = FakeStore(), FakeTools(check=lambda p, a: {"otp_match": True})

    trace = run(spec, case("PASS_FIRST", "SUCCESS"), tools, store)

    assert spec.max_iterations == 3
    assert trace.terminal == "SUCCESS" and trace.passed("SUCCESS")
    assert trace.iterations == 1
    assert trace.ui_updates == ["Looping [Pass 1/3]", COMPLETION]
    assert trace.handoff_rows == 1
    assert store.rows == [
        (
            "auth_event_audit_handoff",
            {
                "iteration_pass": 1,
                "device_fingerprint": "fp-test-1",
                "otp_match": True,
                "access_scope": "none",
            },
        )
    ]
    assert trace.passes[0].gate_eval.model_dump() == {"success": True, "escalation": False}
    assert_consistent(trace, 3)
    json.dumps(trace.to_json_object())  # the trace is plain JSON


def test_e13_3_t02_pass_after_retry_reads_fresh_state() -> None:
    d = ms01_1()
    spec = spec_for(d, success_gate="user_status == 'READY' AND policy_violation == False")
    store, tools = FakeStore(user_status="PENDING"), FakeTools()
    test_case = case("PASS_AFTER_RETRY", "SUCCESS", state_script={2: {"user_status": "READY"}})

    trace = run(spec, test_case, tools, store)

    assert trace.terminal == "SUCCESS" and trace.iterations == 2
    assert trace.passes[0].fresh_state["user_status"] == "PENDING"
    assert trace.passes[1].fresh_state["user_status"] == "READY"
    assert [p.gate_eval.success for p in trace.passes] == [False, True]
    assert store.reads == 3  # the no-op check, then once at the start of every pass
    assert_consistent(trace, 3)


def test_e13_3_missing_state_script_leaves_state_unchanged() -> None:
    d = ms01_1()
    spec = spec_for(d, success_gate="user_status == 'READY'")
    store = FakeStore(user_status="PENDING")
    test_case = case("PASS_AFTER_RETRY", "SUCCESS", state_script={3: {"user_status": "READY"}})

    trace = run(spec, test_case, FakeTools(), store)

    assert [p.fresh_state for p in trace.passes[:2]] == [trace.passes[0].fresh_state] * 2
    assert trace.terminal == "SUCCESS" and trace.iterations == 3


def test_e13_3_t03_escalation_at_exact_bound() -> None:
    d = ms01_1()
    store, tools = FakeStore(), FakeTools()

    trace = run(spec_for(d), case("ESCALATION", "ESCALATED"), tools, store)

    assert tools.count(ACTION) == 3 and tools.count(CHECK) == 3
    assert trace.terminal == "ESCALATED" and trace.iterations == 3
    assert trace.ui_updates == [
        "Looping [Pass 1/3]",
        "Looping [Pass 2/3]",
        "Looping [Pass 3/3]",
        ESCALATION,
    ]
    assert trace.handoff_rows == 3 == len(store.rows)
    assert_consistent(trace, 3)


def test_e13_3_escalation_condition_stops_before_the_bound() -> None:
    d = ms01_1()
    store, tools = FakeStore(), FakeTools()
    test_case = case("ESCALATION", "ESCALATED", state_script={2: {"failed_login_attempts": 3}})

    trace = run(spec_for(d), test_case, tools, store)

    assert trace.terminal == "ESCALATED" and trace.iterations == 2
    assert trace.passes[-1].gate_eval.model_dump() == {"success": False, "escalation": True}
    assert tools.count(ACTION) == 2
    assert trace.ui_updates[-1] == ESCALATION


def test_e13_3_max_iterations_one_failing_check_escalates() -> None:
    d = ms01_1()
    tools = FakeTools()

    trace = run(spec_for(d, max_iterations=1), case("EDGE", "ESCALATED"), tools, FakeStore())

    assert trace.terminal == "ESCALATED" and trace.iterations == 1
    assert trace.ui_updates == ["Looping [Pass 1/1]", ESCALATION]
    assert tools.count(ACTION) == 1


def test_e13_3_t04_no_op_never_calls_the_action() -> None:
    d = ms01_1()
    store, tools = FakeStore(user_status="LOCKED"), FakeTools()

    trace = run(spec_for(d), case("NO_OP", "NO_OP"), tools, store)

    assert tools.count(ACTION) == 0 and tools.calls == []
    assert trace.terminal == "NO_OP" and trace.passed("NO_OP")
    assert trace.iterations == 0 and trace.passes == []
    assert trace.ui_updates == [] and trace.handoff_rows == 0 and store.rows == []


def test_e13_3_t05_budget_overrun_cancels_the_call_and_fails_the_case() -> None:
    d = ms01_1()
    tools = FakeTools(action_sleep=1.5)
    started = time.monotonic()

    trace = run(spec_for(d, budget_limit="1000ms"), case("EDGE", "SUCCESS"), tools, FakeStore())

    elapsed = time.monotonic() - started
    assert 0.9 <= elapsed < 1.4  # cancelled at the budget, not after the full 1.5 s sleep
    assert trace.error is not None
    assert trace.error.category == "TIMEOUT" and trace.error.budget_exceeded
    assert trace.error.step == "action" and trace.error.pass_no == 1
    assert 900 <= trace.error.elapsed_ms < 1400
    assert trace.terminal is None and not trace.passed("SUCCESS")
    assert tools.count(CHECK) == 0 and trace.iterations == 0
    assert trace.to_json_object()["error"]["budget_exceeded"] is True


def test_e13_3_t06_both_gates_true_same_pass_is_success() -> None:
    d = ms01_1()
    tools = FakeTools(check=lambda p, a: {"otp_match": p == 2})
    test_case = case("EDGE", "SUCCESS", state_script={2: {"failed_login_attempts": 3}})

    trace = run(spec_for(d), test_case, tools, FakeStore())

    assert trace.terminal == "SUCCESS" and trace.iterations == 2
    assert trace.passes[1].gate_eval.model_dump() == {"success": True, "escalation": True}
    assert trace.ui_updates[-1] == COMPLETION
    assert_consistent(trace, 3)


def test_e13_3_t07_expression_injection_rejected_before_any_run() -> None:
    d = ms01_1()

    with pytest.raises(LoopSpecError) as excinfo:
        spec_for(d, success_gate="__import__('os').system('id')")

    problems = excinfo.value.problems
    assert [p.path for p in problems] == ["v_variables.success_gate"]
    assert problems[0].code.startswith("EXPR_")
    assert excinfo.value.http_status == 422


def test_e13_3_t08_upper_bound_ten_passes_and_eleven_rejected() -> None:
    d = ms01_1()
    store, tools = FakeStore(), FakeTools()

    trace = run(spec_for(d, max_iterations=10), case("EDGE", "ESCALATED"), tools, store)

    assert trace.terminal == "ESCALATED" and trace.iterations == 10
    assert trace.handoff_rows == 10 == len(store.rows)
    assert [row["iteration_pass"] for _, row in store.rows] == list(range(1, 11))
    assert_consistent(trace, 10)

    with pytest.raises(LoopSpecError) as excinfo:
        spec_for(d, max_iterations=11)
    assert [p.code for p in excinfo.value.problems] == [BOUND_INVALID]
    tampered = spec_for(d).to_json_object() | {"max_iterations": 11}
    with pytest.raises(ValidationError):
        load_loop_spec(tampered)


def test_e13_3_action_exception_is_runtime_error_and_stops() -> None:
    d = ms01_1()
    tools = FakeTools(raises=True)
    store = FakeStore()

    trace = run(spec_for(d), case("EDGE", "SUCCESS"), tools, store)

    assert trace.error is not None and trace.error.category == "RUNTIME_ERROR"
    assert "RuntimeError: tool crashed" in trace.error.message
    assert trace.terminal is None and trace.iterations == 0
    assert tools.calls == [ACTION]  # remaining passes not run, check never called
    assert store.rows == []


def test_e13_3_handoff_write_failure_is_runtime_error() -> None:
    class AppendOnlyViolation(FakeStore):
        def append_handoff(self, table: str, row: Mapping[str, Any]) -> None:
            raise PermissionError("auth_event_audit_handoff is append-only")

    trace = run(spec_for(ms01_1()), case("EDGE", "SUCCESS"), FakeTools(), AppendOnlyViolation())

    assert trace.error is not None and trace.error.category == "RUNTIME_ERROR"
    assert trace.error.step == "handoff" and trace.iterations == 0 and trace.ui_updates == []


def test_e13_3_check_output_missing_gate_field_is_schema_mismatch() -> None:
    class NoOtp(FakeTools):
        async def call(self, tool: str, arguments: Mapping[str, JsonValue]) -> dict[str, Any]:
            self.calls.append(tool)
            return {"access_scope": "none", "auth_status": "REJECTED"}

    trace = run(spec_for(ms01_1()), case("EDGE", "SUCCESS"), NoOtp(), FakeStore())

    assert trace.error is not None and trace.error.category == "OUTPUT_SCHEMA_MISMATCH"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"kind": "SMOKE"}, "kind"),
        ({"expected_terminal": "FAILED"}, "expected_terminal"),
        ({"fixed_check_tool": ACTION}, "must be different tools"),
    ],
)
def test_e13_3_case_input_validation(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        LoopTestCase.model_validate(case("EDGE", "SUCCESS").model_dump() | change)


@pytest.mark.parametrize("bad_key", [0, 4])
def test_e13_3_state_script_keys_are_passes(bad_key: int) -> None:
    tools = FakeTools()
    test_case = case("EDGE", "SUCCESS", state_script={bad_key: {"user_status": "READY"}})

    with pytest.raises(LoopHarnessError, match=r"state_script keys \[\d\] are not passes 1..3"):
        run(spec_for(ms01_1()), test_case, tools, FakeStore())
    assert tools.calls == []


def test_e13_3_missing_required_input_rejected() -> None:
    test_case = LoopTestCase.model_validate(
        case("EDGE", "SUCCESS").model_dump() | {"inputs": {"mobile_number": "+919800000001"}}
    )
    with pytest.raises(LoopHarnessError, match="device_fingerprint"):
        run(spec_for(ms01_1()), test_case, FakeTools(), FakeStore())
