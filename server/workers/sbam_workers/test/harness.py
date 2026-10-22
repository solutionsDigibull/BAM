"""Bounded-loop harness: test-only emulation of a Sub-Skill's loop (TD §15.2, O-17, issue E13.3).

The harness runs one test case against a compiled `loop_spec.json` and returns a trace. It is an
emulation for tests, not the production loop: the semantics it follows are the ones the loop_spec
publishes (`"semantics": "TD §15.2"`), so SRIM can implement the same loop and be compared to it.

    if no_op_condition(state, inputs):          -> NO_OP, the action is never called
    for pass in 1..max_iterations:
        apply state_script[pass] (if any), then re-read fresh state (never cached)
        action tool, then fixed check tool      (each cancelled after budget_ms)
        ui_update 'Looping [Pass p/max]', then append one handoff row
        success_gate                             -> SUCCESS   (checked first)
        escalation_condition                     -> ESCALATED
    no terminal state by the bound               -> ESCALATED

Gates are evaluated only by `loop_expr.evaluate` (the safe grammar); nothing here calls eval/exec.
A tool that raises, overruns its budget or returns output a gate cannot read fails the case: the
trace gets `error` with a sandbox failure category and no terminal state, and no further pass runs.

Tools and state are injected (`ToolClient`, `StateStore`), so unit tests use fakes and the
integration test uses a scratch Postgres schema with an append-only handoff table.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import Any, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from sbam_contracts.isatvon14 import NNotification

from sbam_workers.isatvon import loop_expr
from sbam_workers.isatvon.loop_expr import EvalContext, EvalError
from sbam_workers.isatvon.loop_spec import SEMANTICS_REF, CompiledLoopSpec

Terminal = Literal["SUCCESS", "ESCALATED", "NO_OP"]
CaseKind = Literal["PASS_FIRST", "PASS_AFTER_RETRY", "ESCALATION", "NO_OP", "EDGE", "FDE"]
FailureCategory = Literal[
    "IMPORT_ERROR",
    "RUNTIME_ERROR",
    "TIMEOUT",
    "OOM",
    "OUTPUT_SCHEMA_MISMATCH",
    "ASSERTION_FAILED",
    "EGRESS_BLOCKED",
    "INFRA_ERROR",
]

ITERATION_FIELD: Final = "iteration_pass"  # handoff column the harness fills with the pass number
DEFAULT_COMPLETION: Final = "Status: Completed"
DEFAULT_ESCALATION: Final = "Status: Escalated"
_ESCALATION_TEXT: Final = re.compile(r"escalat", re.IGNORECASE)


def progress_message(pass_no: int, max_iterations: int) -> str:
    return f"Looping [Pass {pass_no}/{max_iterations}]"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LoopTestCase(_Strict):
    """One `test_cases` row as the harness needs it.

    `state_script[p]` is applied to the fresh-state tables right before pass p re-reads them, so
    key 2 is "what changed between pass 1 and pass 2". A pass without a key leaves state unchanged.
    """

    case_id: str = Field(min_length=1)
    kind: CaseKind
    expected_terminal: Terminal
    inputs: dict[str, JsonValue]
    state_script: dict[int, dict[str, JsonValue]] = Field(default_factory=dict)
    action_tool: str = Field(min_length=1)
    fixed_check_tool: str = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct_tools(self) -> "LoopTestCase":
        if self.action_tool == self.fixed_check_tool:
            raise ValueError("action_tool and fixed_check_tool must be different tools")
        return self


class HandoffSpec(_Strict):
    """`o_outcome.state_handoff_file`: the append-only table and the fields every row carries."""

    table: str = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)


class GateEval(_Strict):
    success: bool
    escalation: bool  # both are always evaluated and recorded; success wins a tie (TD §15.2)


class PassRecord(_Strict):
    pass_no: int = Field(alias="pass", ge=1)
    fresh_state: dict[str, JsonValue]
    action_output: dict[str, JsonValue]
    check_output: dict[str, JsonValue]
    action_ms: int = Field(ge=0)
    check_ms: int = Field(ge=0)
    ui_update: str
    gate_eval: GateEval


class HarnessError(_Strict):
    category: FailureCategory
    pass_no: int = Field(alias="pass", ge=1)
    step: Literal["action", "check", "handoff", "gate"]
    message: str
    budget_exceeded: bool = False
    elapsed_ms: int = Field(ge=0)


class HarnessTrace(_Strict):
    """The harness output. A failed case has `error` set and no terminal state."""

    case_id: str
    terminal: Terminal | None
    iterations: int = Field(ge=0)
    passes: list[PassRecord]
    ui_updates: list[str]
    handoff_rows: int = Field(ge=0)
    error: HarnessError | None = None

    def to_json_object(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def passed(self, expected: Terminal) -> bool:
        return self.error is None and self.terminal == expected


class ToolClient(Protocol):
    """Calls one tool registered on the Sub-Skill's FastMCP server and returns its JSON object."""

    def call(self, tool: str, arguments: Mapping[str, JsonValue]) -> Awaitable[dict[str, Any]]: ...


class StateStore(Protocol):
    """The fresh-state tables and the handoff table of one ephemeral database."""

    def read_state(self, names: list[str]) -> dict[str, Any]: ...

    def apply_script(self, changes: Mapping[str, JsonValue]) -> None: ...

    def append_handoff(self, table: str, row: Mapping[str, Any]) -> None: ...


class LoopHarnessError(ValueError):
    """The test case or loop_spec cannot be run at all (raised before any tool is called)."""


class _CaseFailedError(Exception):
    def __init__(self, error: HarnessError) -> None:
        super().__init__(error.message)
        self.error = error


def notification_messages(notification: NNotification) -> tuple[str, str]:
    """(completion, escalation) messages from `n_notification.ui_updates`.

    The escalation message is the first one that mentions escalation; the completion message is the
    last one that does not. Missing either falls back to a generic status line.
    """
    escalation = next((m for m in notification.ui_updates if _ESCALATION_TEXT.search(m)), None)
    others = [m for m in notification.ui_updates if not _ESCALATION_TEXT.search(m)]
    return (
        others[-1] if others else DEFAULT_COMPLETION,
        escalation if escalation is not None else DEFAULT_ESCALATION,
    )


def validate_case(
    spec: CompiledLoopSpec, case: LoopTestCase, required_inputs: list[str] | None = None
) -> None:
    """Reject a case the harness must not run. Raises LoopHarnessError naming every problem."""
    problems: list[str] = []
    if spec.semantics != SEMANTICS_REF:
        problems.append(f"loop_spec semantics {spec.semantics!r} is not {SEMANTICS_REF!r}")
    bad_keys = sorted(k for k in case.state_script if not 1 <= k <= spec.max_iterations)
    if bad_keys:
        problems.append(f"state_script keys {bad_keys} are not passes 1..{spec.max_iterations}")
    missing = sorted(set(required_inputs or []) - set(case.inputs))
    if missing:
        problems.append(f"inputs miss required fields {missing}")
    if problems:
        raise LoopHarnessError("; ".join(problems))


async def _timed(
    step: Literal["action", "check"],
    pass_no: int,
    budget_ms: int,
    call: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], int]:
    started = time.monotonic()

    def elapsed() -> int:
        return round((time.monotonic() - started) * 1000)

    try:
        output = await asyncio.wait_for(call(), timeout=budget_ms / 1000)
    except TimeoutError:
        raise _CaseFailedError(
            HarnessError.model_validate(
                {
                    "category": "TIMEOUT",
                    "pass": pass_no,
                    "step": step,
                    "message": f"{step} tool overran budget_limit {budget_ms} ms and was cancelled",
                    "budget_exceeded": True,
                    "elapsed_ms": elapsed(),
                }
            )
        ) from None
    except Exception as exc:  # any tool failure is the case's RUNTIME_ERROR
        raise _CaseFailedError(
            HarnessError.model_validate(
                {
                    "category": "RUNTIME_ERROR",
                    "pass": pass_no,
                    "step": step,
                    "message": f"{type(exc).__name__}: {exc}",
                    "elapsed_ms": elapsed(),
                }
            )
        ) from exc
    if not isinstance(output, dict):
        raise _CaseFailedError(
            HarnessError.model_validate(
                {
                    "category": "OUTPUT_SCHEMA_MISMATCH",
                    "pass": pass_no,
                    "step": step,
                    "message": f"{step} tool returned {type(output).__name__}, not an object",
                    "elapsed_ms": elapsed(),
                }
            )
        )
    return output, elapsed()


def _gate(ast: Mapping[str, Any], ctx: EvalContext, pass_no: int) -> bool:
    try:
        return loop_expr.evaluate(ast, ctx)
    except EvalError as exc:
        raise _CaseFailedError(
            HarnessError.model_validate(
                {
                    "category": "OUTPUT_SCHEMA_MISMATCH",
                    "pass": pass_no,
                    "step": "gate",
                    "message": f"{exc.code}: {exc.message}",
                    "elapsed_ms": 0,
                }
            )
        ) from None


def _handoff_row(handoff: HandoffSpec, pass_no: int, *scopes: Mapping[str, Any]) -> dict[str, Any]:
    """One row with every required field; later scopes win (inputs < state < check < action)."""
    merged: dict[str, Any] = {}
    for scope in scopes:
        merged.update(scope)
    merged[ITERATION_FIELD] = pass_no
    missing = [f for f in handoff.required_fields if f not in merged]
    if missing:
        raise _CaseFailedError(
            HarnessError.model_validate(
                {
                    "category": "OUTPUT_SCHEMA_MISMATCH",
                    "pass": pass_no,
                    "step": "handoff",
                    "message": f"handoff row misses required fields {missing}",
                    "elapsed_ms": 0,
                }
            )
        )
    return {f: merged[f] for f in handoff.required_fields}


async def run_case(
    spec: CompiledLoopSpec,
    case: LoopTestCase,
    *,
    fresh_state_parameters: list[str],
    handoff: HandoffSpec,
    notification: NNotification,
    tools: ToolClient,
    store: StateStore,
    required_inputs: list[str] | None = None,
) -> HarnessTrace:
    """Run one case to a terminal state or a failure. Invalid cases raise LoopHarnessError."""
    validate_case(spec, case, required_inputs)
    completion_msg, escalation_msg = notification_messages(notification)
    limit = spec.max_iterations
    passes: list[PassRecord] = []
    ui_updates: list[str] = []
    rows = 0

    def trace(terminal: Terminal | None, error: HarnessError | None = None) -> HarnessTrace:
        return HarnessTrace(
            case_id=case.case_id,
            terminal=terminal,
            iterations=len(passes),
            passes=passes,
            ui_updates=ui_updates,
            handoff_rows=rows,
            error=error,
        )

    try:
        seeded = store.read_state(fresh_state_parameters)
        no_op = EvalContext(
            outputs={}, state=seeded, inputs=case.inputs, iteration=0, max_iterations=limit
        )
        if _gate(spec.no_op_condition.ast, no_op, 1):
            return trace("NO_OP")

        for pass_no in range(1, limit + 1):
            if pass_no in case.state_script:
                store.apply_script(case.state_script[pass_no])
            state = store.read_state(fresh_state_parameters)  # every pass, never cached
            arguments: dict[str, JsonValue] = {**case.inputs, ITERATION_FIELD: pass_no}
            action_output, action_ms = await _timed(
                "action", pass_no, spec.budget_ms, partial(tools.call, case.action_tool, arguments)
            )
            check_output, check_ms = await _timed(
                "check",
                pass_no,
                spec.budget_ms,
                partial(tools.call, case.fixed_check_tool, {**arguments, **action_output}),
            )
            ui_update = progress_message(pass_no, limit)
            row = _handoff_row(handoff, pass_no, case.inputs, state, check_output, action_output)
            try:
                store.append_handoff(handoff.table, row)
            except Exception as exc:  # e.g. the append-only trigger
                raise _CaseFailedError(
                    HarnessError.model_validate(
                        {
                            "category": "RUNTIME_ERROR",
                            "pass": pass_no,
                            "step": "handoff",
                            "message": f"{type(exc).__name__}: {exc}",
                            "elapsed_ms": 0,
                        }
                    )
                ) from exc
            rows += 1

            ctx = EvalContext(
                outputs={**action_output, **check_output},
                state=state,
                inputs=case.inputs,
                iteration=pass_no,
                max_iterations=limit,
            )
            success = _gate(spec.success_gate.ast, ctx, pass_no)
            escalation = _gate(spec.escalation_condition.ast, ctx, pass_no)
            ui_updates.append(ui_update)  # one progress message per completed pass
            passes.append(
                PassRecord.model_validate(
                    {
                        "pass": pass_no,
                        "fresh_state": state,
                        "action_output": action_output,
                        "check_output": check_output,
                        "action_ms": action_ms,
                        "check_ms": check_ms,
                        "ui_update": ui_update,
                        "gate_eval": GateEval(success=success, escalation=escalation),
                    }
                )
            )
            if success:
                ui_updates.append(completion_msg)
                return trace("SUCCESS")
            if escalation:
                ui_updates.append(escalation_msg)
                return trace("ESCALATED")
    except _CaseFailedError as failed:
        return trace(None, failed.error)

    ui_updates.append(escalation_msg)  # hard stop: no terminal state within max_iterations
    return trace("ESCALATED")
