"""The Promptfoo suite and its results (TD §15.1, E13.6): how a Sub-Skill's loop tests are judged.

The sandbox runs every test case through the loop harness (E13.3) and returns a trace; the suite
says what that trace must show, and `test_runs` keeps what it did show.

* `Suite` is the generated Promptfoo configuration, built from the manifest's contracts, the V
  bounds and the dataset's cases. Its `suite_sha256` is the one canonical hash (E13.6-T09): the same
  inputs always give the same hash, and certification compares it to decide whether a result may be
  reused.
* Deterministic assertions only decide pass or fail on their own. `llm_rubric` is allowed only for a
  free-text output field and always comes after the deterministic ones (the model enforces the
  order); a rubric can add a failure, never remove one. There is no percentage threshold: a run
  passes only if every case passes.
* `TestRunOut` is a stored run, `LoopTestsBadge` the cell the Build & Test grid shows for it
  (`NON_CERTIFYING` when the sandbox run was `local-docker`, `BLOCKED` when the infrastructure could
  not give a verdict).
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.canonical import sha256_of
from sbam_contracts.testgen import MIN_CASES, CaseKind, CaseName

SUITE_VERSION: Final = "1"
MAX_RESULTS: Final = 2_000  # cases x assertions
MAX_MESSAGE: Final = 500


class AssertionType(StrEnum):
    """What a suite assertion checks. The values are the `Assertion.type` a test case may carry."""

    OUTPUT_SCHEMA = "output_schema"  # outputs validate against the manifest's output_schema
    STATE_HANDOFF_ID = "state_handoff_id"  # the output carries `state_handoff_id`
    TERMINAL_STATE = "terminal_state"  # the harness ended in the case's expected terminal state
    EXPECTED_OUTPUT = "expected_output"  # the declared outputs are present with those values
    ITERATIONS_WITHIN_BOUND = "iterations_within_bound"  # passes run <= max_iterations
    CYCLE_TIME_WITHIN_BUDGET = "cycle_time_within_budget"  # every check took <= budget_limit
    HANDOFF_ROWS = "handoff_rows"  # one handoff row per pass, with every required field
    UI_UPDATES = "ui_updates"  # `Looping [Pass x/y]` in order, then the closing message
    LLM_RUBRIC = "llm_rubric"  # a free-text field only; never overrides a deterministic failure


DETERMINISTIC: Final = frozenset(t for t in AssertionType if t is not AssertionType.LLM_RUBRIC)


class TestRunState(StrEnum):
    __test__ = False

    PASS = "PASS"  # noqa: S105 - a result name, not a credential
    FAIL = "FAIL"
    NON_CERTIFYING = "NON_CERTIFYING"  # a local-docker run: valid evidence, never certifies
    BLOCKED = "BLOCKED"  # the infrastructure gave no verdict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuiteAssertion(_Strict):
    type: AssertionType
    value: JsonValue = None  # the type's parameter (e.g. the rubric text, the expected updates)


class SuiteBounds(_Strict):
    """The V bounds the assertions read, as the manifest declares them."""

    max_iterations: Annotated[int, Field(ge=1, le=10)]
    budget_ms: Annotated[int, Field(ge=1)]  # `budget_limit`, parsed to integer milliseconds
    handoff_fields: list[Annotated[str, Field(min_length=1, max_length=63)]] = Field(
        default_factory=list, max_length=200
    )  # the handoff table's required fields


class SuiteCase(_Strict):
    name: CaseName
    kind: CaseKind
    input: dict[str, JsonValue]
    expected_terminal: Literal["SUCCESS", "ESCALATED", "NO_OP"]
    assertions: list[SuiteAssertion] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _deterministic_first(self) -> "SuiteCase":
        seen_rubric = False
        for assertion in self.assertions:
            if assertion.type is AssertionType.LLM_RUBRIC:
                seen_rubric = True
            elif seen_rubric:
                raise ValueError("deterministic assertions must come before an llm_rubric")
        if self.assertions[0].type is AssertionType.LLM_RUBRIC:
            raise ValueError("a case needs a deterministic assertion before any llm_rubric")
        return self


class Suite(_Strict):
    """The suite for one Sub-Skill build: at least three cases, all of which must pass."""

    suite_version: Literal["1"] = SUITE_VERSION
    bounds: SuiteBounds
    output_schema: dict[str, JsonValue]
    cases: list[SuiteCase] = Field(min_length=MIN_CASES, max_length=20)

    @model_validator(mode="after")
    def _distinct_names(self) -> "Suite":
        names = [c.name for c in self.cases]
        if len(set(names)) != len(names):
            raise ValueError("case names must be distinct")
        return self


def suite_sha256(suite: Suite) -> str:
    """64 hex over the canonical suite, cases in name order so their order does not matter."""
    document = suite.model_dump(mode="json")
    document["cases"] = sorted(document["cases"], key=lambda case: case["name"])
    return sha256_of(document).removeprefix("sha256:")


class AssertionResult(_Strict):
    case: CaseName
    type: AssertionType
    passed: bool
    message: Annotated[str, Field(max_length=MAX_MESSAGE)] = ""


class LoopTestsBadge(_Strict):
    """The 'Loop tests' cell of the Build & Test grid: `passed/total` and a state."""

    state: TestRunState
    passed: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _bounded(self) -> "LoopTestsBadge":
        if self.passed > self.total:
            raise ValueError("passed cannot exceed total")
        return self


class TestRunOut(_Strict):
    """A stored Promptfoo run (`test_runs`): per case per assertion, and the totals by case."""

    __test__ = False

    id: UUID
    sub_skill_id: UUID
    code_attempt_id: UUID
    sandbox_run_id: UUID
    suite_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    state: TestRunState
    total: Annotated[int, Field(ge=MIN_CASES)]
    passed: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]
    non_certifying: bool = False
    results: list[AssertionResult] = Field(max_length=MAX_RESULTS)
    report_key: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _totals(self) -> "TestRunOut":
        if self.passed + self.failed != self.total:
            raise ValueError("passed + failed must equal total")
        if self.state is TestRunState.PASS and (self.failed or self.non_certifying):
            raise ValueError("PASS needs every case passed on a certifying run")
        if self.non_certifying and self.state is TestRunState.BLOCKED:
            raise ValueError("a blocked run has no verdict to be non-certifying about")
        return self


class TestRunsResponse(_Strict):
    """`GET W/sub-skills/{sub_id}/test-runs`: the runs, newest first (empty before any)."""

    __test__ = False

    runs: list[TestRunOut] = Field(default_factory=list, max_length=100)


def badge_of(run: TestRunOut) -> LoopTestsBadge:
    """The grid cell for a stored run (its state already says `NON_CERTIFYING` for local-docker)."""
    return LoopTestsBadge(state=run.state, passed=run.passed, total=run.total)


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/promptfoo/`."""
    models: dict[str, type[BaseModel]] = {
        "suite": Suite,
        "test_run": TestRunOut,
        "test_runs_response": TestRunsResponse,
    }
    return {stem: contract_schema(model, f"promptfoo/{stem}") for stem, model in models.items()}
