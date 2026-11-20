"""The build timeline (#926, WG-39): one build id through every model call, code attempt and gate.

`GET W/timeline` answers, from the outside, "is this build stuck, which stage failed, what did it
cost". It is derived from rows the build already wrote (`audit_log` transitions, `llm_calls`,
`code_attempts`, `gate_results`, the run mirror); nothing new is stored.

The build id is the Temporal workflow id of the build (`build-<sub_skill_id>-<build_no>`): every
`llm_calls` row of the build carries it. A step never carries a model output, a document content,
a secret or a finding's free text: gate findings are named by gate, code and file position only.
Money is a string with four decimals, as in `llm_usage`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.state_machines import SubSkillState, WorkflowState

Money = Annotated[str, Field(pattern=r"^\d+\.\d{4}$")]


class StepKind(StrEnum):
    TRANSITION = "TRANSITION"  # the Sub-Skill's state changed (audit row)
    MODEL = "MODEL"  # the model calls of one activity (llm_calls), re-asks included
    CODE_ATTEMPT = "CODE_ATTEMPT"  # one generated code attempt (code_attempts)
    GATE = "GATE"  # one gate's result for a build attempt (gate_results)


class StepStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"
    RUNNING = "RUNNING"  # the newest step of a build that is still active


class TimelineFinding(BaseModel):
    """A gate finding by name only: which gate, which rule code, where. No message text."""

    model_config = ConfigDict(extra="forbid")

    gate: GateName
    code: Annotated[str, Field(min_length=1, max_length=100)]
    file: Annotated[str | None, Field(max_length=500)] = None
    line: Annotated[int | None, Field(ge=1)] = None
    package: Annotated[str | None, Field(max_length=200)] = None


class TimelineStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: Annotated[int, Field(ge=1)]
    kind: StepKind
    # TRANSITION: the event ("gate_failed"); MODEL: the activity id; CODE_ATTEMPT: its status;
    # GATE: the gate name.
    name: Annotated[str, Field(min_length=1, max_length=255)]
    status: StepStatus
    started_at: datetime
    duration_ms: Annotated[int | None, Field(ge=0)] = None  # to the next step; null for the last
    cycle: Annotated[int | None, Field(ge=0)] = None
    attempt: Annotated[int | None, Field(ge=0)] = None
    from_state: SubSkillState | None = None  # TRANSITION only
    to_state: SubSkillState | None = None  # TRANSITION only
    alias: Annotated[str | None, Field(max_length=100)] = None  # MODEL: the model alias
    calls: Annotated[int, Field(ge=0)] = 0  # MODEL: attempts made, failed ones included
    tokens_in: Annotated[int, Field(ge=0)] = 0
    tokens_out: Annotated[int, Field(ge=0)] = 0
    cost_usd: Money = "0.0000"
    findings: list[TimelineFinding] = Field(default_factory=list)


class TimelineFailure(BaseModel):
    """Why the build is parked or failed: the last transition's event and the failing gates."""

    model_config = ConfigDict(extra="forbid")

    event: Annotated[str, Field(min_length=1, max_length=100)]  # e.g. generation_failed
    state: SubSkillState
    at: datetime
    failing_gates: list[GateName] = Field(default_factory=list)
    findings: list[TimelineFinding] = Field(default_factory=list)


class SubSkillTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sub_skill_id: UUID
    skill_id: str
    skill_name: str
    state: SubSkillState
    build_id: Annotated[str | None, Field(max_length=255)] = None  # null before the first build
    build_no: Annotated[int | None, Field(ge=1)] = None
    started_at: datetime | None = None
    last_activity_at: datetime | None = None  # a stuck build shows an old value here
    current_stage: Annotated[str | None, Field(max_length=100)] = None
    code_attempts: Annotated[int, Field(ge=0)] = 0
    llm_calls: Annotated[int, Field(ge=0)] = 0
    tokens_in: Annotated[int, Field(ge=0)] = 0
    tokens_out: Annotated[int, Field(ge=0)] = 0
    cost_usd: Money = "0.0000"
    failure: TimelineFailure | None = None
    steps: list[TimelineStep] = Field(default_factory=list)


class BuildTimelineResponse(BaseModel):
    """`GET W/timeline`: every Sub-Skill's latest build, oldest step first."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: UUID
    workflow_state: WorkflowState
    cost_usd: Money
    sub_skills: list[SubSkillTimeline]


def schemas() -> dict[str, dict[str, Any]]:
    models: dict[str, type[BaseModel]] = {"timeline_response": BuildTimelineResponse}
    return {stem: contract_schema(model, f"build/{stem}") for stem, model in models.items()}
