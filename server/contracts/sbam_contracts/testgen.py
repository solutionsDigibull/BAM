"""Test data (TD §15.1, E13.4; C-29, C-30): the loop scenarios a Sub-Skill is tested with.

A *dataset* is one version of a Sub-Skill's synthetic test data: its cases, the files its
`external_data` FILE refs read, and the mocks for its API refs. Every FDE edit writes a new version
with a new hash and keeps the old one as evidence. A *case* is one loop scenario: the inputs, the
fresh-state rows to seed (SQL), the scripted state changes between passes (retry scenarios), the
terminal state the harness must reach, and the assertions.

* `TestgenOutput` is what `sbam-testgen` may answer. The model cannot set platform fields
  (the `FDE` kind, `synthetic`, `status`, ids): the model rejects them.
* `TestsResponse`, `CasePut`, `CasePost` are the bodies of `GET`, `PUT` and `POST`
  `W/sub-skills/{sub_id}/tests[/cases[/{case_id}]]` (TD §8.8). The `ETag` is the dataset's `sha256`:
  an edit is a new dataset version, so it is the thing a concurrent writer must have seen.
* `dataset_sha256` is the one canonical hash (E13.4-T09). The API and the worker both use it.

Rules the contract cannot express on its own and the backend enforces: each case's `input` validates
against the Sub-Skill's `contracts.input_schema`; the seed SQL passes the statement allowlist
(INSERT / UPDATE on `v_variables.tables`, at most `MAX_SEED_ROWS` rows).
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.canonical import sha256_of

MIN_CASES: Final = 3  # PASS_FIRST, ESCALATION and one more
MAX_GENERATED_CASES: Final = 8
MAX_CASES: Final = 20  # generated and FDE cases together
MAX_SEED_ROWS: Final = 500  # per case
MAX_FILE_BYTES: Final = 5 * 1024 * 1024
MAX_STATEMENTS: Final = 500
MAX_PASS_STEPS: Final = 10  # state_script: one step per pass boundary, max_iterations is 1 to 10
MAX_ASSERTIONS: Final = 50
MAX_REASKS: Final = 3  # E13.4: re-ask the model at most this many times on a schema failure


class CaseKind(StrEnum):
    PASS_FIRST = "PASS_FIRST"  # noqa: S105 - a scenario name; success on the first pass
    PASS_AFTER_RETRY = "PASS_AFTER_RETRY"  # noqa: S105 - the check fails, then succeeds
    ESCALATION = "ESCALATION"  # never meets success_gate: escalates at max_iterations
    NO_OP = "NO_OP"  # no_op_condition is true: no action is executed
    EDGE = "EDGE"  # boundary inputs
    FDE = "FDE"  # an FDE's own case; never produced by the model


class ExpectedTerminal(StrEnum):
    SUCCESS = "SUCCESS"
    ESCALATED = "ESCALATED"
    NO_OP = "NO_OP"


GENERATED_KINDS: Final = frozenset(
    {
        CaseKind.PASS_FIRST,
        CaseKind.PASS_AFTER_RETRY,
        CaseKind.ESCALATION,
        CaseKind.NO_OP,
        CaseKind.EDGE,
    }
)
REQUIRED_KINDS: Final = frozenset({CaseKind.PASS_FIRST, CaseKind.ESCALATION})
THIRD_KINDS: Final = frozenset({CaseKind.PASS_AFTER_RETRY, CaseKind.NO_OP, CaseKind.EDGE})
_ANY: Final = frozenset(ExpectedTerminal)
# The terminal state each kind leads to (EDGE and FDE cases may end in any).
KIND_TERMINAL: Final[Mapping[CaseKind, frozenset[ExpectedTerminal]]] = {
    CaseKind.PASS_FIRST: frozenset({ExpectedTerminal.SUCCESS}),
    CaseKind.PASS_AFTER_RETRY: frozenset({ExpectedTerminal.SUCCESS}),
    CaseKind.ESCALATION: frozenset({ExpectedTerminal.ESCALATED}),
    CaseKind.NO_OP: frozenset({ExpectedTerminal.NO_OP}),
    CaseKind.EDGE: _ANY,
    CaseKind.FDE: _ANY,
}

CaseName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
SqlText = Annotated[str, Field(min_length=1, max_length=20_000)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Assertion(_Strict):
    """An expectation beyond the terminal state and the output (an `N` assertion: the `ui_updates`
    the harness must emit, a handoff row, a pass count)."""

    type: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,59}$")]
    value: JsonValue = None


class CaseBody(_Strict):
    """What a case holds, whoever wrote it."""

    name: CaseName
    input: dict[str, JsonValue]
    initial_state: list[SqlText] = Field(default_factory=list, max_length=MAX_STATEMENTS)
    state_script: list[list[SqlText]] = Field(default_factory=list, max_length=MAX_PASS_STEPS)
    expected_terminal: ExpectedTerminal
    expected_output: dict[str, JsonValue] | None = None
    assertions: list[Assertion] = Field(default_factory=list, max_length=MAX_ASSERTIONS)


class GeneratedCase(CaseBody):
    """One case of the model's answer: a generated kind and a terminal state that kind can reach."""

    kind: CaseKind

    @model_validator(mode="after")
    def _kind_and_terminal(self) -> "GeneratedCase":
        if self.kind not in GENERATED_KINDS:
            raise ValueError(f"{self.kind.value} is not a kind the model may produce")
        if self.expected_terminal not in KIND_TERMINAL[self.kind]:
            allowed = ", ".join(sorted(t.value for t in KIND_TERMINAL[self.kind]))
            raise ValueError(
                f"a {self.kind.value} case ends in {allowed}, not {self.expected_terminal.value}"
            )
        return self


class TestgenOutput(_Strict):
    """What `sbam-testgen` may answer (E13.4): at least three cases, PASS_FIRST and ESCALATION and
    one more of PASS_AFTER_RETRY, NO_OP or EDGE, with distinct names."""

    __test__ = False  # not a pytest class

    cases: list[GeneratedCase] = Field(min_length=MIN_CASES, max_length=MAX_GENERATED_CASES)

    @model_validator(mode="after")
    def _the_required_scenarios(self) -> "TestgenOutput":
        names = [c.name for c in self.cases]
        if len(set(names)) != len(names):
            raise ValueError("case names must be distinct")
        kinds = {c.kind for c in self.cases}
        missing = sorted(k.value for k in REQUIRED_KINDS - kinds)
        if missing:
            raise ValueError(f"missing required scenario kinds: {', '.join(missing)}")
        if not kinds & THIRD_KINDS:
            raise ValueError("a third scenario is required: PASS_AFTER_RETRY, NO_OP or EDGE")
        return self


class TestCaseOut(CaseBody):
    """A stored case, as the API answers it."""

    __test__ = False

    id: UUID
    kind: CaseKind


class TestDatasetOut(_Strict):
    """One version of a Sub-Skill's test data. `synthetic` is always true."""

    __test__ = False

    version: Annotated[int, Field(ge=1)]
    sha256: Sha256Hex
    synthetic: Literal[True] = True
    edited_by_fde: bool
    files_keys: list[str]
    mocks_keys: list[str] = Field(
        default_factory=list, description="the mock definitions of its API references (E13.5)"
    )
    created_at: datetime
    cases: list[TestCaseOut]


class TestsResponse(_Strict):
    """`GET W/sub-skills/{sub_id}/tests`: the current dataset, or none before generation. `ETag` is
    its `sha256`. (The last Promptfoo results join this answer with E13.6.)"""

    __test__ = False

    dataset: TestDatasetOut | None = None


class CasePut(_Strict):
    """`PUT W/sub-skills/{sub_id}/tests/cases/{case_id}` with `If-Match`: the parts of a case an FDE
    may change. `name` and `kind` are fixed; at least one field must be sent."""

    input: dict[str, JsonValue] | None = None
    initial_state: list[SqlText] | None = Field(default=None, max_length=MAX_STATEMENTS)
    state_script: list[list[SqlText]] | None = Field(default=None, max_length=MAX_PASS_STEPS)
    expected_terminal: ExpectedTerminal | None = None
    expected_output: dict[str, JsonValue] | None = None
    assertions: list[Assertion] | None = Field(default=None, max_length=MAX_ASSERTIONS)

    @model_validator(mode="after")
    def _something_to_change(self) -> "CasePut":
        if not self.model_fields_set:
            raise ValueError("nothing to change: send at least one field")
        return self


class CasePost(CaseBody):
    """`POST W/sub-skills/{sub_id}/tests/cases` with `If-Match`: a new case; the server sets the
    kind (`FDE`), so it is not part of the body."""


def case_canonical(kind: CaseKind, case: CaseBody) -> dict[str, Any]:
    """The fields of a case that its hash covers, in one fixed shape."""
    return {
        "name": case.name,
        "kind": kind.value,
        "input": case.input,
        "initial_state": case.initial_state,
        "state_script": case.state_script,
        "expected_terminal": case.expected_terminal.value,
        "expected_output": case.expected_output,
        "assertions": [a.model_dump(mode="json") for a in case.assertions],
    }


def dataset_sha256(
    cases: Sequence[tuple[CaseKind, CaseBody]],
    files: Mapping[str, str] | None = None,
    mocks: Mapping[str, str] | None = None,
) -> str:
    """64 hex: the canonical hash of a dataset (E13.4-T09). The cases are taken in name order, so
    the order they are stored or sent in does not matter; `files` and `mocks` map a name to the
    sha256 of its content. The same data always gives the same hash, and any edit gives another."""
    ordered = sorted(cases, key=lambda pair: pair[1].name)
    document = {
        "cases": [case_canonical(kind, case) for kind, case in ordered],
        "files": dict(sorted((files or {}).items())),
        "mocks": dict(sorted((mocks or {}).items())),
    }
    return sha256_of(document).removeprefix("sha256:")


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/testgen/`."""
    models: dict[str, type[BaseModel]] = {
        "testgen_output": TestgenOutput,
        "tests_response": TestsResponse,
        "case_put": CasePut,
        "case_post": CasePost,
    }
    return {stem: contract_schema(model, f"testgen/{stem}") for stem, model in models.items()}
