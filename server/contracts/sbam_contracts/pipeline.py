"""ProjectPipeline contracts (TD §13.2, E10.1): stages, signals, status query and stage DTOs.

The pipeline runs as one Temporal workflow per project (`pipeline-<project_id>`). Every activity
and child takes and returns one of these models: IDs only, never content (TD §13.1). Stage
activities have fixed names; the issues that implement them (ingest, knowledge, SOW, #91
create_workflow, #107 SubSkillBuild, certification) replace the stub bodies and keep these types.
"""

from enum import StrEnum
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts.runs import RunInput
from sbam_contracts.state_machines import Actor, MachineName

PROJECT_PIPELINE: Final = "ProjectPipeline"  # workflow type name
SUB_SKILL_BUILD: Final = "SubSkillBuild"
MAX_PARALLEL_BUILDS: Final = 4  # TD §13.2
MAX_PARALLEL_LIMIT: Final = 16  # E10.3: build.max_parallel is 1..16
SHA256_HEX: Final = r"^[0-9a-f]{64}$"
ActorId = Annotated[str, Field(min_length=1, max_length=255)]


class PipelineStage(StrEnum):
    """The pipeline's stages in order (TD §13.2); FAILED / CANCELLED end it early."""

    INGEST = "ingest"
    KNOWLEDGE = "knowledge"
    OKF = "okf"
    SOW = "sow"
    WAIT_SOW_APPROVAL = "wait_sow_approval"
    CREATE_WORKFLOW = "create_workflow"
    EXTRACT_MACROS = "extract_macros"
    EXTRACT_SUB_SKILLS = "extract_sub_skills"
    WAIT_GENERATE = "wait_generate"
    BUILD_SCHEMA_SQL = "build_schema_sql"
    BUILD_SUB_SKILLS = "build_sub_skills"
    WAIT_REBUILD = "wait_rebuild"  # a Sub-Skill needs FDE review; waits for rebuild_subskills
    CERTIFY_MACROS = "certify_macros"
    VALIDATE_WORKFLOW = "validate_workflow"
    CERTIFY_WORKFLOW = "certify_workflow"
    FREEZE = "freeze"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


ORDER: Final = tuple(PipelineStage)[: list(PipelineStage).index(PipelineStage.DONE) + 1]
WAIT_STAGES: Final = frozenset(
    {PipelineStage.WAIT_SOW_APPROVAL, PipelineStage.WAIT_GENERATE, PipelineStage.WAIT_REBUILD}
)


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- start + signals -----------------------------------------------------------------------------


class PipelineStartRequest(_Dto):
    """Body of POST P/pipeline/start (optional): documents to ingest. #69 fills it from the DB."""

    document_version_ids: list[UUID] = Field(default_factory=list)


class SowApproved(_Dto):
    sow_version_id: UUID
    sha256: Annotated[str, Field(pattern=SHA256_HEX)]
    actor_id: ActorId


class BuildPlan(_Dto):
    """E10.3: read once per generate (`plan_builds`), so a config change mid-run never alters the
    schedule. `depends_on[s]` = Sub-Skills whose outputs feed `s`; they must be CERTIFIED first."""

    max_parallel: Annotated[int, Field(ge=1, le=MAX_PARALLEL_LIMIT)] = MAX_PARALLEL_BUILDS
    depends_on: dict[UUID, list[UUID]] = Field(default_factory=dict)


class ResumeState(_Dto):
    """What a continue-as-new carries over (TD §13.2: long human waits)."""

    stage: PipelineStage
    workflow_id: UUID | None = None
    sub_skill_ids: list[UUID] = Field(default_factory=list)
    needs_review: list[UUID] = Field(default_factory=list)  # Sub-Skills waiting for a rebuild
    seen_keys: list[str] = Field(default_factory=list)
    reminders_sent: list[PipelineStage] = Field(default_factory=list)
    sow_approved: SowApproved | None = None
    builds_started: dict[str, int] = Field(default_factory=dict)  # sub_skill_id -> last build_no
    certified: list[UUID] = Field(default_factory=list)
    blocked: list[UUID] = Field(
        default_factory=list
    )  # in needs_review: an upstream never certified
    build_plan: BuildPlan | None = None
    refs: dict[str, UUID] = Field(default_factory=dict)  # stage results, e.g. knowledge, okf


class PipelineInput(RunInput):
    """Start input. `actor_id` is the FDE who started it (FDE events are applied in their name)."""

    actor_id: ActorId
    document_version_ids: list[UUID] = Field(default_factory=list)
    search_attributes: bool = True  # False only where the namespace has none (test server)
    # Continue-as-new at a wait once history passes this many events (None: when Temporal suggests).
    continue_as_new_after: Annotated[int | None, Field(ge=1)] = None
    resume: ResumeState | None = None
    sow_hint: Annotated[str | None, Field(max_length=2000)] = None  # FDE's regenerate hint (E6.3)


class Generate(_Dto):
    sub_skill_ids: list[UUID] = Field(default_factory=list)  # empty = all Sub-Skills
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    actor_id: ActorId


class RebuildSubSkills(_Dto):
    sub_skill_ids: list[UUID] = Field(min_length=1)
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    actor_id: ActorId


class Cancel(_Dto):
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    actor_id: ActorId


class PipelineStatus(_Dto):
    """`status` query result; the API reads it (e.g. #90 checks the pipeline waits for approval)."""

    stage: PipelineStage
    waiting_for: str | None = None  # signal name while at a wait stage
    workflow_id: UUID | None = None
    sub_skills_total: int = 0
    sub_skills_done: int = 0
    failed_stage: PipelineStage | None = None
    last_error: str | None = None
    reminders_sent: list[PipelineStage] = Field(default_factory=list)


class BuildProgress(_Dto):
    """`build_schedule` query (E10.3): the current build round plus whole-run counts."""

    running: list[UUID] = Field(default_factory=list)
    queued: list[UUID] = Field(default_factory=list)  # in start order
    blocked: list[UUID] = Field(default_factory=list)
    max_parallel: int = MAX_PARALLEL_BUILDS
    certified: int = 0
    needs_review: int = 0


# --- stage activity DTOs (stubs now; real activities keep these) ---------------------------------


class IngestInput(RunInput):
    document_version_ids: list[UUID] = Field(default_factory=list)


class StageRef(RunInput):
    """Input of stages that work on one id (knowledge version, OKF bundle, ...)."""

    ref_id: UUID


class SowGenerateInput(StageRef):
    """`generate_sow`'s input: the knowledge version, and the FDE's hint on a regeneration (a short
    instruction they typed, never document content)."""

    hint: Annotated[str | None, Field(max_length=2000)] = None


class WorkflowRef(RunInput):
    workflow_id: UUID


class MacroRef(WorkflowRef):
    macro_skill_id: UUID


class CreateWorkflowInput(RunInput):
    """#91: re-checks the SOW is APPROVED and `sha256` matches (HashMismatch is non-retryable)."""

    sow_version_id: UUID
    sha256: Annotated[str, Field(pattern=SHA256_HEX)]


class CreateWorkflowResult(_Dto):
    workflow_id: UUID


class BuildPlanInput(WorkflowRef):
    """E10.3 `plan_builds`: every Sub-Skill of the Workflow (edges to other ids are rejected)."""

    sub_skill_ids: list[UUID]


class SubSkillBuildInput(WorkflowRef):
    """#107: the SubSkillBuild child (id `build-<sub_skill_id>-<build_no>`)."""

    sub_skill_id: UUID
    build_no: Annotated[int, Field(ge=1)]


class SubSkillBuildResult(_Dto):
    sub_skill_id: UUID
    certified: bool


class IdResult(_Dto):
    id: UUID


class IdsResult(_Dto):
    ids: list[UUID]


class FreezeResult(_Dto):
    workflow_id: UUID
    version: Annotated[str, Field(pattern=r"^\d+\.0\.\d+$")]


# --- state + notifications -----------------------------------------------------------------------


class TransitionRequest(_Dto):
    """One state-machine transition, applied by the `apply_pipeline_transition` activity."""

    tenant_id: UUID
    project_id: UUID
    machine: MachineName
    entity_id: UUID
    event: str
    actor: Actor
    actor_id: ActorId
    correlation_id: str
    idempotency_key: str
    create: bool = False  # creation event (e.g. sow_approved creates the Workflow)


class TransitionOutcome(_Dto):
    applied: bool  # False: no table registered for the machine yet, or a replayed key
    to_state: str | None = None


class NotifyRequest(_Dto):
    tenant_id: UUID
    project_id: UUID
    type: Annotated[str, Field(min_length=1, max_length=100)]  # e.g. "pipeline.reminder"
    entity_type: str
    entity_id: UUID
    payload: dict[str, str] = Field(default_factory=dict)
    correlation_id: str
    idempotency_key: str
