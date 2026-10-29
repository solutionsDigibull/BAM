"""State machines as code: seven lifecycles, their transitions and who triggers them (TD §14, E0.6).

Machines: project pipeline, Workflow, document version, SOW version, Sub-Skill, Agent (TD: Macro
Skill) and knowledge version (E5.2). Each transition is a row `(from_state, event, to_state,
actor)`; a row with `from_state=None` creates the entity in its first state. The tables are the
single definition used by the state-machine service, the Temporal activities, the DB CHECK
constraints and, read-only through `schemas/state_machines.json`, the UI.

Rules worth knowing
  * SBAM creates the Workflow. The only way to create one is the SYSTEM event `sow_approved`; the
    FDE never creates a Workflow.
  * Failure states never advance: they only go back to the stage that failed (retry), or end.
  * APPROVED / CERTIFIED / FROZEN states have no edit events. Changes after a freeze happen only
    through version events (`start_patch_version`, `scope_changed`, `new_version`).
  * `can_transition` is the authoritative server-side check. The UI may read the tables but never
    decides.
  * Regenerate the JSON with `make contracts`; `make contracts-check` fails if it drifts.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.manifest import ManifestStatus


class Actor(StrEnum):
    FDE = "FDE"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"


class MachineName(StrEnum):
    PROJECT_PIPELINE = "project_pipeline"
    WORKFLOW = "workflow"
    DOCUMENT_VERSION = "document_version"
    SOW_VERSION = "sow_version"
    SUB_SKILL = "sub_skill"
    AGENT = "agent"
    KNOWLEDGE_VERSION = "knowledge_version"


class ProjectPipelineState(StrEnum):
    IDLE = "IDLE"
    INGESTING = "INGESTING"
    KNOWLEDGE_BUILDING = "KNOWLEDGE_BUILDING"
    SOW_GENERATING = "SOW_GENERATING"
    SOW_REVIEW = "SOW_REVIEW"
    SOW_APPROVED = "SOW_APPROVED"
    WORKFLOW_CREATED = "WORKFLOW_CREATED"
    INGESTION_FAILED = "INGESTION_FAILED"
    KNOWLEDGE_FAILED = "KNOWLEDGE_FAILED"
    SOW_GENERATION_FAILED = "SOW_GENERATION_FAILED"
    CANCELLED = "CANCELLED"


class WorkflowState(StrEnum):
    CREATED = "CREATED"
    ISATVON_EXTRACTING = "ISATVON_EXTRACTING"
    ISATVON_REVIEW = "ISATVON_REVIEW"
    BUILDING = "BUILDING"
    CERTIFYING = "CERTIFYING"
    CERTIFIED = "CERTIFIED"
    FROZEN = "FROZEN"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    NEEDS_FDE_REVIEW = "NEEDS_FDE_REVIEW"
    WORKFLOW_VALIDATION_FAILED = "WORKFLOW_VALIDATION_FAILED"
    CANCELLED = "CANCELLED"


class DocumentVersionState(StrEnum):
    UPLOADED = "UPLOADED"
    SCANNING = "SCANNING"
    REJECTED = "REJECTED"
    SCAN_FAILED = "SCAN_FAILED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    PARSE_FAILED = "PARSE_FAILED"
    DELETED = "DELETED"
    PURGED = "PURGED"


class SowVersionState(StrEnum):
    GENERATED = "GENERATED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    GENERATION_FAILED = "GENERATION_FAILED"


class KnowledgeVersionState(StrEnum):
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"
    OUTDATED = "OUTDATED"


class SubSkillState(StrEnum):
    DRAFT = "DRAFT"
    VALID = "VALID"
    INVALID = "INVALID"
    BUILDING = "BUILDING"
    CORRECTING = "CORRECTING"
    TESTING = "TESTING"
    TESTING_BLOCKED = "TESTING_BLOCKED"
    NEEDS_FDE_REVIEW = "NEEDS_FDE_REVIEW"
    DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
    BUILD_ERROR = "BUILD_ERROR"
    CERTIFIED = "CERTIFIED"
    FROZEN = "FROZEN"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


class AgentState(StrEnum):
    """Agent = TD Macro Skill (§14.5)."""

    PENDING = "PENDING"
    BUILDING = "BUILDING"
    CERTIFIED = "CERTIFIED"
    FROZEN = "FROZEN"


class InvalidTransitionCode(StrEnum):
    UNKNOWN_MACHINE = "UNKNOWN_MACHINE"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    UNKNOWN_EVENT = "UNKNOWN_EVENT"
    FORBIDDEN_ACTOR = "FORBIDDEN_ACTOR"
    LOCKED = "LOCKED"
    NOT_ALLOWED = "NOT_ALLOWED"


class InvalidTransition(ValueError):  # noqa: N818 - name fixed by issue E0.6 and TD §14
    def __init__(self, code: InvalidTransitionCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Transition:
    from_state: str | None  # None: the event creates the entity
    event: str
    to_state: str
    actor: Actor


@dataclass(frozen=True)
class Machine:
    name: MachineName
    states: type[StrEnum]
    transitions: tuple[Transition, ...]
    terminal: frozenset[str]
    locked: frozenset[str]  # edit events here fail with LOCKED, not NOT_ALLOWED

    def state_values(self) -> tuple[str, ...]:
        return tuple(s.value for s in self.states)


EDIT_EVENTS: Final = frozenset({"edit"})
F, A, S = Actor.FDE, Actor.ADMIN, Actor.SYSTEM


def _rows(*rows: tuple[str | None, str, str, Actor]) -> tuple[Transition, ...]:
    return tuple(Transition(f, e, t, a) for f, e, t, a in rows)


def _from_each(
    states: Iterable[str], event: str, to: str, actor: Actor
) -> list[tuple[str | None, str, str, Actor]]:
    return [(s, event, to, actor) for s in states]


P = ProjectPipelineState
PROJECT_PIPELINE: Final = Machine(
    MachineName.PROJECT_PIPELINE,
    ProjectPipelineState,
    _rows(
        (None, "create_project", P.IDLE, A),
        (P.IDLE, "start_processing", P.INGESTING, F),
        (P.INGESTING, "ingestion_completed", P.KNOWLEDGE_BUILDING, S),
        (P.INGESTING, "ingestion_failed", P.INGESTION_FAILED, S),
        (P.KNOWLEDGE_BUILDING, "knowledge_ready", P.SOW_GENERATING, S),
        (P.KNOWLEDGE_BUILDING, "knowledge_failed", P.KNOWLEDGE_FAILED, S),
        (P.SOW_GENERATING, "sow_generated", P.SOW_REVIEW, S),
        (P.SOW_GENERATING, "sow_generation_failed", P.SOW_GENERATION_FAILED, S),
        (P.SOW_REVIEW, "approve_sow", P.SOW_APPROVED, F),
        (P.SOW_APPROVED, "workflow_created", P.WORKFLOW_CREATED, S),
        # scope change: new documents, or a new SOW version from the approved one
        (P.WORKFLOW_CREATED, "start_processing", P.INGESTING, F),
        (P.WORKFLOW_CREATED, "start_scope_change", P.SOW_REVIEW, F),
        # failures go back to the stage that failed, never forward
        (P.INGESTION_FAILED, "retry", P.INGESTING, F),
        (P.KNOWLEDGE_FAILED, "retry", P.KNOWLEDGE_BUILDING, F),
        (P.SOW_GENERATION_FAILED, "regenerate", P.SOW_GENERATING, F),
        *_from_each(
            (
                P.IDLE,
                P.INGESTING,
                P.KNOWLEDGE_BUILDING,
                P.SOW_GENERATING,
                P.SOW_REVIEW,
                P.INGESTION_FAILED,
                P.KNOWLEDGE_FAILED,
                P.SOW_GENERATION_FAILED,
            ),
            "cancel",
            P.CANCELLED,
            F,
        ),
    ),
    terminal=frozenset({P.CANCELLED}),
    locked=frozenset(),
)

W = WorkflowState
WORKFLOW: Final = Machine(
    MachineName.WORKFLOW,
    WorkflowState,
    _rows(
        (None, "sow_approved", W.CREATED, S),  # the only way a Workflow comes to exist
        (W.CREATED, "start_extraction", W.ISATVON_EXTRACTING, S),
        (W.ISATVON_EXTRACTING, "extraction_completed", W.ISATVON_REVIEW, S),
        (W.ISATVON_EXTRACTING, "extraction_failed", W.EXTRACTION_FAILED, S),
        (W.EXTRACTION_FAILED, "retry", W.ISATVON_EXTRACTING, F),
        (W.ISATVON_REVIEW, "generate", W.BUILDING, F),
        (W.BUILDING, "all_sub_skills_certified", W.CERTIFYING, S),
        (W.BUILDING, "sub_skill_needs_review", W.NEEDS_FDE_REVIEW, S),
        (W.NEEDS_FDE_REVIEW, "retry", W.BUILDING, F),
        (W.CERTIFYING, "workflow_certified", W.CERTIFIED, S),
        (W.CERTIFYING, "workflow_validation_failed", W.WORKFLOW_VALIDATION_FAILED, S),
        (W.WORKFLOW_VALIDATION_FAILED, "retry", W.BUILDING, F),
        (W.CERTIFIED, "freeze", W.FROZEN, S),
        (
            W.FROZEN,
            "start_patch_version",
            W.BUILDING,
            F,
        ),  # next PATCH; the frozen files stay as they are
        *_from_each(
            (
                W.CREATED,
                W.ISATVON_EXTRACTING,
                W.ISATVON_REVIEW,
                W.BUILDING,
                W.CERTIFYING,
                W.EXTRACTION_FAILED,
                W.NEEDS_FDE_REVIEW,
                W.WORKFLOW_VALIDATION_FAILED,
            ),
            "cancel",
            W.CANCELLED,
            F,
        ),
    ),
    terminal=frozenset({W.CANCELLED}),
    locked=frozenset({W.CERTIFIED, W.FROZEN}),
)

D = DocumentVersionState
DOCUMENT_VERSION: Final = Machine(
    MachineName.DOCUMENT_VERSION,
    DocumentVersionState,
    _rows(
        (None, "upload", D.UPLOADED, F),
        (D.UPLOADED, "scan_started", D.SCANNING, S),
        (D.SCANNING, "scan_clean", D.PARSING, S),
        (D.SCANNING, "malware_detected", D.REJECTED, S),
        (D.SCANNING, "scan_unavailable", D.SCAN_FAILED, S),
        (D.SCAN_FAILED, "auto_retry", D.SCANNING, S),
        (D.SCAN_FAILED, "retry", D.SCANNING, F),
        (D.PARSING, "parse_completed", D.PARSED, S),
        (D.PARSING, "parse_failed", D.PARSE_FAILED, S),
        # Type detection runs after the scan, on a CLEAN version that is already PARSING (E4.4): a
        # type that does not match the content, or is not supported, rejects it (code: reject_code).
        (D.PARSING, "type_rejected", D.REJECTED, S),
        # Re-runs the whole DocumentIngestion: the scan finds the version in SCANNING and carries
        # on, then detect and parse follow (E4.13).
        (D.PARSE_FAILED, "retry", D.SCANNING, F),
        *_from_each(
            (D.UPLOADED, D.PARSED, D.REJECTED, D.SCAN_FAILED, D.PARSE_FAILED),
            "delete",
            D.DELETED,
            F,
        ),
        (
            D.DELETED,
            "restore",
            D.UPLOADED,
            F,
        ),  # within 90 days; restored files are scanned and parsed again
        (D.DELETED, "purge", D.PURGED, S),  # after 90 days
    ),
    terminal=frozenset({D.PURGED}),
    locked=frozenset(),
)

V = SowVersionState
SOW_VERSION: Final = Machine(
    MachineName.SOW_VERSION,
    SowVersionState,
    _rows(
        (None, "generated", V.GENERATED, S),
        (None, "generation_failed", V.GENERATION_FAILED, S),
        (
            None,
            "new_version",
            V.IN_REVIEW,
            F,
        ),  # scope change: draft copied from the approved version
        (V.GENERATION_FAILED, "regenerate", V.GENERATED, F),
        (V.GENERATED, "open_review", V.IN_REVIEW, S),
        (V.GENERATED, "approve", V.APPROVED, F),  # E7.1: approved as generated, without edits
        (V.IN_REVIEW, "edit", V.IN_REVIEW, F),
        (V.IN_REVIEW, "approve", V.APPROVED, F),
        (V.APPROVED, "superseded", V.SUPERSEDED, S),  # a newer version was approved
    ),
    terminal=frozenset({V.SUPERSEDED}),
    locked=frozenset({V.APPROVED, V.SUPERSEDED}),
)

K = SubSkillState
SUB_SKILL: Final = Machine(
    MachineName.SUB_SKILL,
    SubSkillState,
    _rows(
        (None, "extracted", K.DRAFT, S),
        # Scope change (E7.2): a Sub-Skill copied into the new MAJOR Workflow version. Unaffected
        # and certified: reused as it is; affected by the change: invalidated, then re_extracted.
        (None, "reused", K.CERTIFIED, S),
        (None, "carried_invalidated", K.INVALIDATED, S),
        (K.DRAFT, "validation_passed", K.VALID, S),
        (K.DRAFT, "validation_failed", K.INVALID, S),
        (K.VALID, "edit", K.DRAFT, F),
        (K.INVALID, "edit", K.DRAFT, F),
        (K.VALID, "generate", K.BUILDING, F),
        (K.BUILDING, "tests_started", K.TESTING, S),
        (K.BUILDING, "gate_failed", K.CORRECTING, S),
        (K.BUILDING, "dependency_missing", K.DEPENDENCY_FAILED, S),
        (K.BUILDING, "template_error", K.BUILD_ERROR, S),
        # The model could not answer (E11.2c, #483): schema re-asks or transport retries are spent.
        # Nothing is wrong with the Sub-Skill, so the FDE's `retry` (below) tries again.
        (K.BUILDING, "generation_failed", K.NEEDS_FDE_REVIEW, S),
        (K.CORRECTING, "correction_applied", K.BUILDING, S),
        (K.CORRECTING, "attempts_exhausted", K.NEEDS_FDE_REVIEW, S),
        # A correction changed what the Sub-Skill means (#446): not certified on its own say-so. The
        # patch stays in the isatvon_corrected audit row; the FDE's `retry` runs a new cycle from
        # the approved ISATVON. (DEPENDENCY_FAILED, a failure state, only leaves to BUILDING.)
        (K.CORRECTING, "isatvon_review_required", K.NEEDS_FDE_REVIEW, S),
        (K.NEEDS_FDE_REVIEW, "retry", K.BUILDING, F),
        (K.TESTING, "tests_passed", K.CERTIFIED, S),
        (K.TESTING, "tests_failed", K.CORRECTING, S),
        (K.TESTING, "sandbox_unavailable", K.TESTING_BLOCKED, S),
        (K.TESTING_BLOCKED, "auto_retry", K.TESTING, S),
        (K.TESTING_BLOCKED, "retry", K.TESTING, F),
        (K.DEPENDENCY_FAILED, "correction_applied", K.BUILDING, S),
        (K.DEPENDENCY_FAILED, "retry", K.BUILDING, F),
        (
            K.BUILD_ERROR,
            "retry",
            K.BUILDING,
            A,
        ),  # platform defect: fixed and retried by the platform team
        (K.CERTIFIED, "freeze", K.FROZEN, S),
        *_from_each((K.VALID, K.INVALID, K.CERTIFIED, K.FROZEN), "scope_changed", K.INVALIDATED, S),
        (K.INVALIDATED, "re_extracted", K.DRAFT, S),
        (K.FROZEN, "start_patch_version", K.DRAFT, F),
        (K.FROZEN, "superseded", K.SUPERSEDED, S),
    ),
    terminal=frozenset({K.SUPERSEDED}),
    locked=frozenset({K.CERTIFIED, K.FROZEN, K.SUPERSEDED}),
)

G = AgentState
AGENT: Final = Machine(
    MachineName.AGENT,
    AgentState,
    _rows(
        (None, "extracted", G.PENDING, S),
        (None, "carried_over", G.PENDING, S),  # scope change (E7.2): copied into the new version
        (G.PENDING, "build_started", G.BUILDING, S),
        (G.BUILDING, "all_sub_skills_certified", G.CERTIFIED, S),
        (G.CERTIFIED, "sub_skill_invalidated", G.BUILDING, S),
        (G.CERTIFIED, "sub_skill_patched", G.BUILDING, S),
        (G.CERTIFIED, "freeze", G.FROZEN, S),
        (G.FROZEN, "start_patch_version", G.BUILDING, S),
    ),
    terminal=frozenset(),
    locked=frozenset({G.CERTIFIED, G.FROZEN}),
)

KV = KnowledgeVersionState
KNOWLEDGE_VERSION: Final = Machine(
    MachineName.KNOWLEDGE_VERSION,
    KnowledgeVersionState,
    _rows(
        (None, "build_started", KV.BUILDING, S),  # pipeline knowledge stage
        (None, "rebuild", KV.BUILDING, F),  # FDE: POST P/knowledge/rebuild
        (KV.BUILDING, "build_ready", KV.READY, S),
        (KV.BUILDING, "build_failed", KV.FAILED, S),
        (KV.READY, "superseded", KV.OUTDATED, S),  # a newer version became READY
    ),
    terminal=frozenset({KV.FAILED, KV.OUTDATED}),
    locked=frozenset({KV.READY, KV.OUTDATED}),
)

MACHINES: Final[Mapping[MachineName, Machine]] = {
    m.name: m
    for m in (
        PROJECT_PIPELINE,
        WORKFLOW,
        DOCUMENT_VERSION,
        SOW_VERSION,
        SUB_SKILL,
        AGENT,
        KNOWLEDGE_VERSION,
    )
}

# SubSkillState -> skill_metadata.status / governance.lifecycle.state (TD §14.4)
MANIFEST_STATUS: Final[Mapping[SubSkillState, ManifestStatus]] = {
    K.DRAFT: ManifestStatus.DRAFT,
    K.VALID: ManifestStatus.DRAFT,
    K.INVALID: ManifestStatus.DRAFT,
    K.INVALIDATED: ManifestStatus.DRAFT,
    K.BUILDING: ManifestStatus.VALIDATING,
    K.CORRECTING: ManifestStatus.VALIDATING,
    K.TESTING: ManifestStatus.VALIDATING,
    K.TESTING_BLOCKED: ManifestStatus.VALIDATING,
    K.NEEDS_FDE_REVIEW: ManifestStatus.VALIDATING,
    K.DEPENDENCY_FAILED: ManifestStatus.VALIDATING,
    K.BUILD_ERROR: ManifestStatus.VALIDATING,
    K.CERTIFIED: ManifestStatus.APPROVED,
    K.FROZEN: ManifestStatus.APPROVED,
    K.SUPERSEDED: ManifestStatus.SUPERSEDED,
}


# --- Checks ---------------------------------------------------------------------------------------


def _check_definition(machine: Machine) -> None:
    """Fail at import if a table is inconsistent: unknown states, duplicate (from, event) pairs, a
    terminal state with outgoing edges, or edit events out of locked states."""
    states = set(machine.state_values())
    seen: set[tuple[str | None, str]] = set()
    for row in machine.transitions:
        if (row.from_state, row.event) in seen:
            raise ValueError(f"{machine.name}: duplicate ({row.from_state}, {row.event})")
        seen.add((row.from_state, row.event))
        for state in (row.from_state, row.to_state):
            if state is not None and state not in states:
                raise ValueError(f"{machine.name}: unknown state {state}")
        if row.from_state in machine.terminal:
            raise ValueError(f"{machine.name}: terminal {row.from_state} has an outgoing edge")
        if row.from_state in machine.locked and row.event in EDIT_EVENTS:
            raise ValueError(f"{machine.name}: locked {row.from_state} has an edit event")
    if not machine.terminal <= states or not machine.locked <= states:
        raise ValueError(f"{machine.name}: terminal/locked state not in the enum")


for _machine in MACHINES.values():
    _check_definition(_machine)


def machine(name: MachineName | str) -> Machine:
    try:
        return MACHINES[MachineName(name)]
    except ValueError:
        raise InvalidTransition(
            InvalidTransitionCode.UNKNOWN_MACHINE, f"no machine {name!r}"
        ) from None


def can_transition(
    name: MachineName | str, from_state: str | None, event: str, actor: Actor | str
) -> str:
    """The state `event` leads to from `from_state` when `actor` triggers it.

    `from_state=None` asks whether `event` may create the entity. Raises InvalidTransition.
    """
    m = machine(name)
    if from_state is not None and from_state not in m.state_values():
        raise InvalidTransition(
            InvalidTransitionCode.UNKNOWN_STATE, f"{m.name} has no state {from_state!r}"
        )
    if event not in {row.event for row in m.transitions}:
        raise InvalidTransition(
            InvalidTransitionCode.UNKNOWN_EVENT, f"{m.name} has no event {event!r}"
        )
    row = next((r for r in m.transitions if r.from_state == from_state and r.event == event), None)
    if row is None:
        if from_state in m.locked and event in EDIT_EVENTS:
            raise InvalidTransition(
                InvalidTransitionCode.LOCKED, f"{m.name} {from_state} is locked"
            )
        where = "creation" if from_state is None else from_state
        raise InvalidTransition(
            InvalidTransitionCode.NOT_ALLOWED, f"{m.name}: {event!r} not allowed from {where}"
        )
    if row.actor != actor:
        raise InvalidTransition(
            InvalidTransitionCode.FORBIDDEN_ACTOR,
            f"{m.name}: {event!r} is a {row.actor} event, not {actor}",
        )
    return row.to_state


def create(name: MachineName | str, event: str, actor: Actor | str) -> str:
    """The first state of a new entity created by `event`; raises InvalidTransition."""
    return can_transition(name, None, event, actor)


def manifest_status(state: SubSkillState) -> ManifestStatus:
    return MANIFEST_STATUS[state]


def check_constraint_values(name: MachineName | str) -> tuple[str, ...]:
    """Allowed values for the status column's CHECK constraint (Alembic), in enum order."""
    return machine(name).state_values()


# --- Export ---------------------------------------------------------------------------------------


def export() -> dict[str, Any]:
    """Read-only tables for the UI, written to `schemas/state_machines.json` by `make contracts`."""
    return {
        m.name.value: {
            "states": list(m.state_values()),
            "initial": sorted({r.to_state for r in m.transitions if r.from_state is None}),
            "terminal": sorted(m.terminal),
            "locked": sorted(m.locked),
            "transitions": [
                {"from": r.from_state, "event": r.event, "to": r.to_state, "actor": r.actor.value}
                for r in m.transitions
            ],
        }
        for m in MACHINES.values()
    }


class StateMachineStates(BaseModel):
    """Every state enum in one model, so the generated TypeScript exports them as string unions."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    actor: Actor
    machine: MachineName
    project_pipeline: ProjectPipelineState
    workflow: WorkflowState
    document_version: DocumentVersionState
    sow_version: SowVersionState
    sub_skill: SubSkillState
    agent: AgentState
    knowledge_version: KnowledgeVersionState


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/state_machines/`."""
    return {"states": contract_schema(StateMachineStates, "state_machines/states")}
