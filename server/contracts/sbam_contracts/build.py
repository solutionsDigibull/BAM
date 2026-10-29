"""SubSkillBuild stage contracts (TD §13.2 / §13.3, E10.2).

The `SubSkillBuild` child runs the build stages as activities with fixed names. Every stage takes a
`BuildStageInput` and returns IDs only (`IdResult`; findings and code live in the DB / storage).
The pipeline-level DTOs (`SubSkillBuildInput`, `SubSkillBuildResult`) stay in `pipeline.py`.

Error contract (activity -> workflow): gates raise a non-retryable `ApplicationError` whose `type`
is one of the constants below and whose first detail is the list of persisted finding ids.
"""

from enum import StrEnum
from typing import Annotated, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts.pipeline import ActorId, WorkflowRef
from sbam_contracts.state_machines import SubSkillState

MAX_CORRECTIONS: Final = 2  # TD §13.2: at most 2 correction attempts per build cycle
RETRY_SIGNAL: Final = "retry"
CANCEL_SIGNAL: Final = "cancel"
BUILD_STATUS_QUERY: Final = "build_status"

# ApplicationError.type values (TD §13.3)
GATE_FAILED: Final = "GateFailed"  # ast_gate / static_gate: non-retryable, goes to `correct`
TESTS_FAILED: Final = "TestsFailed"  # run_sandbox_tests: the tests ran and failed -> `correct`
# infrastructure trouble in run_sandbox_tests: TESTING_BLOCKED, never `correct`
SANDBOX_UNAVAILABLE: Final = "SandboxUnavailable"
# render_skeleton / assemble: BUILD_ERROR, a platform defect an admin fixes
TEMPLATE_ERROR: Final = "TemplateError"
# check_libraries: no runtime registry for the pinned image digest; non-retryable, an operator
# fixes it (E9.3). Never `correct`.
REGISTRY_UNAVAILABLE: Final = "RegistryUnavailable"


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BuildStageInput(WorkflowRef):
    """Input of every build stage. `(sub_skill_id, build_no, cycle, attempt, <stage>)` is the
    idempotency key of everything a stage persists (gate_results, code_attempts, ...).

    `cycle` counts FDE retries of one build (0 first); `attempt` counts the correction loop inside
    a cycle (0 = first pass, 1 and 2 = after a correction). Both are needed: a retry starts
    attempt 0 again, and its evidence must not overwrite the failed attempts' (TD §13.4).
    """

    sub_skill_id: UUID
    build_no: Annotated[int, Field(ge=1)]
    cycle: Annotated[int, Field(ge=0)] = 0
    attempt: Annotated[int, Field(ge=0, le=MAX_CORRECTIONS)] = 0


class LibraryStatus(StrEnum):
    OK = "OK"
    MISSING = "MISSING"  # a required library is not available -> DEPENDENCY_FAILED


class LibraryCheckResult(_Dto):
    status: LibraryStatus
    finding_ids: list[UUID] = Field(default_factory=list)


class CorrectionInput(BuildStageInput):
    """`correct` reads the failing findings and returns the corrected bodies (stored, id only)."""

    failed_gate: Annotated[str, Field(min_length=1, max_length=100)]
    finding_ids: list[UUID] = Field(default_factory=list)


class SubSkillRetry(_Dto):
    """`retry` signal: the FDE fixed the cause (or wants another go). Sent by the API after
    authorization; `idempotency_key` is the request's Idempotency-Key."""

    reason: Annotated[str, Field(min_length=1, max_length=500)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    actor_id: ActorId


class RetryRequest(_Dto):
    """Body of POST W/sub-skills/{sub_id}/retry (with an Idempotency-Key header)."""

    reason: Annotated[str, Field(min_length=1, max_length=500)]


class RetryAccepted(_Dto):
    """202 body: the build that was told to retry (a repeat of the same key answers the same)."""

    sub_skill_id: UUID
    build_no: int


class BuildStage(StrEnum):
    """Where a build is, for the `build_status` query (the activities' names, TD §13.2)."""

    BUILD_MANIFEST = "build_manifest"
    CHECK_LIBRARIES = "check_libraries"
    RENDER_SKELETON = "render_skeleton"
    GENERATE_BODIES = "generate_bodies"
    ASSEMBLE = "assemble"
    AST_GATE = "ast_gate"
    STATIC_GATE = "static_gate"
    GENERATE_TESTS = "generate_tests"
    BUILD_PROMPTFOO_SUITE = "build_promptfoo_suite"
    RUN_SANDBOX_TESTS = "run_sandbox_tests"
    CERTIFY = "certify_sub_skill"
    CORRECT = "correct"
    WAIT_RETRY = "wait_retry"  # NEEDS_FDE_REVIEW / DEPENDENCY_FAILED / TESTING_BLOCKED
    DONE = "done"


class SubSkillBuildStatus(_Dto):
    """`build_status` query result. The API reads it to resolve a retry (tenant, project and
    Workflow must match the URL, else 404) and to answer a repeated Idempotency-Key."""

    tenant_id: UUID
    project_id: UUID
    workflow_id: UUID
    sub_skill_id: UUID
    build_no: int
    state: SubSkillState
    stage: BuildStage
    cycle: int = 0
    attempt: int = 0
    corrections_used: int = 0
    waiting_for_retry: bool = False
    finding_ids: list[UUID] = Field(default_factory=list)
    retry_keys: list[str] = Field(default_factory=list)  # Idempotency-Keys already signalled
