"""The Build & Test dashboard's data (TD §8.10, §9.3, E12.6): Sub-Skills by gates, and the
findings of one gate.

TD §8.10 lists the routes but leaves their bodies open ("per Sub-Skill: stage, attempt, gate
summary"; "gate results with findings"); these are the shapes the dashboard (#121) is built against
until the endpoints and the `gate_results` table land (#117 / #119).

- `GET W/builds` → `BuildsResponse`: one row per Sub-Skill with a status for every gate column.
- `GET W/sub-skills/{sub_id}/gates?attempt=` → `GatesResponse`: each gate's status and findings.
- Retry is `POST W/sub-skills/{sub_id}/retry` (`sbam_contracts.build.RetryRequest`, E10.2).
"""

from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.build import MAX_CORRECTIONS, BuildStage
from sbam_contracts.promptfoo import LoopTestsBadge
from sbam_contracts.state_machines import SubSkillState, WorkflowState

Attempt = Annotated[int, Field(ge=0, le=MAX_CORRECTIONS)]


class GateName(StrEnum):
    """The dashboard's gate columns, in display order (TD §7 `gate_results.gate` plus the two the
    dashboard adds: CODEGEN for generation itself and LOOP_TESTS for the sandbox loop tests)."""

    ISATVON_CONTRACT = "ISATVON_CONTRACT"
    LIBRARIES = "LIBRARIES"
    SCHEMA_SQL = "SCHEMA_SQL"
    CODEGEN = "CODEGEN"
    AST_SYNTAX = "AST_SYNTAX"
    AST_SKELETON = "AST_SKELETON"
    AST_CONFORMANCE = "AST_CONFORMANCE"
    AST_DECOUPLING = "AST_DECOUPLING"
    AST_NO_MANUAL_LOOP = "AST_NO_MANUAL_LOOP"
    AST_HANDOFF = "AST_HANDOFF"
    AST_IMPORTS = "AST_IMPORTS"
    AST_SQL_SAFETY = "AST_SQL_SAFETY"
    RUFF = "RUFF"
    BLACK = "BLACK"
    MYPY = "MYPY"
    CLAMAV = "CLAMAV"
    BANDIT = "BANDIT"
    IMPORT_RESOLUTION = "IMPORT_RESOLUTION"
    LOOP_TESTS = "LOOP_TESTS"


class GateStatus(StrEnum):
    PENDING = "PENDING"  # not reached yet in this attempt
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # not run: an earlier gate failed, or it doesn't apply to this Sub-Skill


class Severity(StrEnum):
    """How serious a static-gate finding is (E12.4). A gate fails on a HIGH finding; LOW and MEDIUM
    ones are reported (Bandit's own scale; Ruff, Black, MyPy and ClamAV findings are all HIGH)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Finding(BaseModel):
    """One problem a gate found; the shape the build worker writes into `gate_results.findings`.
    `file` / `line` point into the generated bundle, so the UI can open the Code Viewer there.
    LIBRARIES findings (E9.3) name the `package`; `expected` is what was requested, `actual` the
    registry's version (null when it has none) and `message` the guidance. The static gates
    (E12.4) set `severity`; the AST gates leave it out."""

    model_config = ConfigDict(extra="forbid")

    gate: GateName
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(max_length=10_000)
    file: str | None = Field(default=None, max_length=500)
    line: int | None = Field(default=None, ge=1)
    col: int | None = Field(default=None, ge=0)
    expected: str | None = Field(default=None, max_length=1_000)
    actual: str | None = Field(default=None, max_length=1_000)
    package: str | None = Field(default=None, max_length=200)  # LIBRARIES: the normalized name
    severity: Severity | None = None  # static gates (E12.4): what the gate blocks on is HIGH


class BuildRow(BaseModel):
    """One Sub-Skill's latest build: where it is, which attempt, and every gate's status."""

    model_config = ConfigDict(extra="forbid")

    sub_skill_id: UUID
    skill_id: str
    skill_name: str
    state: SubSkillState
    stage: BuildStage
    attempt: Attempt = 0
    # a gate missing from the map is PENDING; keys are only ever GateName values
    gates: dict[GateName, GateStatus] = Field(default_factory=dict)
    loop_tests: LoopTestsBadge | None = None  # E13.6: absent until a Promptfoo run exists


class BuildsResponse(BaseModel):
    """`GET W/builds`. `active` is true while any Sub-Skill is still building or testing; the UI
    polls only then."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: UUID
    workflow_state: WorkflowState
    active: bool
    sub_skills: list[BuildRow]


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: GateName
    status: GateStatus
    findings: list[Finding] = Field(default_factory=list)
    tool_version: str | None = None


class GatesResponse(BaseModel):
    """`GET W/sub-skills/{sub_id}/gates?attempt=`: one attempt's gate results, in GateName order."""

    model_config = ConfigDict(extra="forbid")

    sub_skill_id: UUID
    attempt: Attempt
    gates: list[GateResult]


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/build/`."""
    models: dict[str, type[BaseModel]] = {
        "builds_response": BuildsResponse,
        "gates_response": GatesResponse,
    }
    return {stem: contract_schema(model, f"build/{stem}") for stem, model in models.items()}
