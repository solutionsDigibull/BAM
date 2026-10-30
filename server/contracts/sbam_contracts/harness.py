"""The loop harness trace (TD §15.2, E13.3): what one test case did, pass by pass.

The harness (`sbam_workers.test.harness`) emulates a Sub-Skill's bounded loop for one test case
and returns a `HarnessTrace`. The Promptfoo assertions (E13.6) judge it, and the test panel (E13.7)
shows it: the fresh state each pass read, the action and check outputs, the `ui_update`, the
durations and the gate evaluation. A failed case has `error` set and no terminal state.

* `TestRunTraces` is the body of `GET W/sub-skills/{sub_id}/test-runs/{run_id}/traces`: one trace
  per case of a stored run, empty when the run recorded none.
"""

from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from sbam_contracts._jsonschema import contract_schema

MAX_TRACES: Final = 20  # testgen.MAX_CASES: one trace per case

Terminal = Literal["SUCCESS", "ESCALATED", "NO_OP"]
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


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class TestRunTraces(BaseModel):
    """`GET W/sub-skills/{sub_id}/test-runs/{run_id}/traces`: the run's traces, one per case."""

    __test__ = False

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    traces: Annotated[list[HarnessTrace], Field(max_length=MAX_TRACES)] = Field(
        default_factory=list
    )


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/promptfoo/`."""
    return {"test_run_traces": contract_schema(TestRunTraces, "promptfoo/test_run_traces")}
