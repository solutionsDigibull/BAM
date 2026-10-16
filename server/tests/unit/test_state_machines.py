"""E0.6 unit tests: the six state machines (TD §14): transitions, actors, reachability, export."""

import json
import re
from collections import deque

import pytest
from sbam_contracts.gen import STATE_MACHINES_FILE, _json
from sbam_contracts.manifest import ManifestStatus
from sbam_contracts.state_machines import (
    MACHINES,
    Actor,
    InvalidTransition,
    InvalidTransitionCode,
    Machine,
    MachineName,
    SubSkillState,
    Transition,
    can_transition,
    check_constraint_values,
    create,
    export,
    manifest_status,
)

ALL_ROWS = [(m, row) for m in MACHINES.values() for row in m.transitions]


def code_of(*args: object) -> InvalidTransitionCode:
    with pytest.raises(InvalidTransition) as info:
        can_transition(*args)  # type: ignore[arg-type]
    return info.value.code


# --- T01: every allowed transition ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("m", "row"), ALL_ROWS, ids=[f"{m.name}:{r.from_state}:{r.event}" for m, r in ALL_ROWS]
)
def test_e0_6_t01_every_row_is_allowed(m: Machine, row: Transition) -> None:
    assert can_transition(m.name, row.from_state, row.event, row.actor) == row.to_state


def test_e0_6_six_machines_with_upper_snake_states() -> None:
    assert [m.value for m in MACHINES] == [
        "project_pipeline",
        "workflow",
        "document_version",
        "sow_version",
        "sub_skill",
        "agent",
    ]
    for m in MACHINES.values():
        assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*", s) for s in m.state_values()), m.name


def test_e0_6_no_duplicate_from_event_pairs() -> None:
    for m in MACHINES.values():
        pairs = [(r.from_state, r.event) for r in m.transitions]
        assert len(pairs) == len(set(pairs)), m.name


# --- T02: only SBAM creates a Workflow ------------------------------------------------------------


def test_e0_6_t02_fde_cannot_create_a_workflow() -> None:
    assert (
        code_of(MachineName.WORKFLOW, None, "create", Actor.FDE)
        == InvalidTransitionCode.UNKNOWN_EVENT
    )
    assert code_of(MachineName.WORKFLOW, None, "sow_approved", Actor.FDE) == (
        InvalidTransitionCode.FORBIDDEN_ACTOR
    )
    assert create(MachineName.WORKFLOW, "sow_approved", Actor.SYSTEM) == "CREATED"
    creations = [r for r in MACHINES[MachineName.WORKFLOW].transitions if r.from_state is None]
    assert creations == [Transition(None, "sow_approved", "CREATED", Actor.SYSTEM)]


def test_e0_6_t02_pipeline_reaches_workflow_created_only_through_system() -> None:
    assert can_transition("project_pipeline", "SOW_APPROVED", "workflow_created", "SYSTEM") == (
        "WORKFLOW_CREATED"
    )
    into_created = [
        r
        for r in MACHINES[MachineName.PROJECT_PIPELINE].transitions
        if r.to_state == "WORKFLOW_CREATED"
    ]
    assert [(r.from_state, r.actor) for r in into_created] == [("SOW_APPROVED", Actor.SYSTEM)]


# --- T03-T06: forbidden transitions ---------------------------------------------------------------


def test_e0_6_t03_pipeline_cannot_skip_stages() -> None:
    assert (
        code_of("project_pipeline", "IDLE", "approve_sow", "FDE")
        == InvalidTransitionCode.NOT_ALLOWED
    )
    assert code_of("project_pipeline", "INGESTING", "sow_generated", "SYSTEM") == (
        InvalidTransitionCode.NOT_ALLOWED
    )


def test_e0_6_t04_approved_sow_is_locked() -> None:
    assert code_of("sow_version", "APPROVED", "edit", "FDE") == InvalidTransitionCode.LOCKED
    assert can_transition("sow_version", "IN_REVIEW", "edit", "FDE") == "IN_REVIEW"


def test_e0_6_t04_scope_change_supersedes_the_previous_approved_sow() -> None:
    assert create("sow_version", "new_version", "FDE") == "IN_REVIEW"
    assert can_transition("sow_version", "APPROVED", "superseded", "SYSTEM") == "SUPERSEDED"


def test_e0_6_t05_rejected_document_cannot_be_parsed() -> None:
    for event in ("parse_completed", "scan_clean", "retry"):
        assert (
            code_of("document_version", "REJECTED", event, "SYSTEM")
            == InvalidTransitionCode.NOT_ALLOWED
        )


def test_e4_4_type_detection_rejects_a_parsing_document_only_as_the_system() -> None:
    assert can_transition("document_version", "PARSING", "type_rejected", "SYSTEM") == "REJECTED"
    assert (
        code_of("document_version", "PARSING", "type_rejected", "FDE")
        == InvalidTransitionCode.FORBIDDEN_ACTOR
    )
    # nothing else can be type-rejected: it needs a CLEAN scan first, and a parsed file is settled
    for state in ("UPLOADED", "SCANNING", "PARSED", "SCAN_FAILED", "PARSE_FAILED", "REJECTED"):
        assert (
            code_of("document_version", state, "type_rejected", "SYSTEM")
            == InvalidTransitionCode.NOT_ALLOWED
        )


def test_e0_6_t06_needs_review_cannot_jump_to_certified() -> None:
    assert code_of("sub_skill", "NEEDS_FDE_REVIEW", "tests_passed", "SYSTEM") == (
        InvalidTransitionCode.NOT_ALLOWED
    )
    exits = [
        r for r in MACHINES[MachineName.SUB_SKILL].transitions if r.from_state == "NEEDS_FDE_REVIEW"
    ]
    assert exits == [Transition("NEEDS_FDE_REVIEW", "retry", "BUILDING", Actor.FDE)]


# --- Edge cases from the issue --------------------------------------------------------------------


def test_e0_6_testing_blocked_retries_automatically_and_by_fde() -> None:
    assert can_transition("sub_skill", "TESTING_BLOCKED", "auto_retry", "SYSTEM") == "TESTING"
    assert can_transition("sub_skill", "TESTING_BLOCKED", "retry", "FDE") == "TESTING"


def test_e0_6_one_sub_skill_in_review_does_not_stop_the_others() -> None:
    # Sub-Skills are separate entities: one in NEEDS_FDE_REVIEW leaves others free to keep building.
    assert can_transition("sub_skill", "BUILDING", "tests_started", "SYSTEM") == "TESTING"
    assert (
        can_transition("workflow", "BUILDING", "sub_skill_needs_review", "SYSTEM")
        == "NEEDS_FDE_REVIEW"
    )


def test_e0_6_deleted_document_restores_or_is_purged() -> None:
    assert can_transition("document_version", "DELETED", "restore", "FDE") == "UPLOADED"
    assert can_transition("document_version", "DELETED", "purge", "SYSTEM") == "PURGED"
    assert (
        code_of("document_version", "PURGED", "restore", "FDE") == InvalidTransitionCode.NOT_ALLOWED
    )


def test_e0_6_sow_generation_failure_regenerates() -> None:
    assert create("sow_version", "generation_failed", "SYSTEM") == "GENERATION_FAILED"
    assert can_transition("sow_version", "GENERATION_FAILED", "regenerate", "FDE") == "GENERATED"


# --- Safety checks --------------------------------------------------------------------------------

# Every failure state may only go back to the stage that failed, or end (cancel / delete).
FAILURE_EXITS = {
    "project_pipeline": {
        "INGESTION_FAILED": {"INGESTING", "CANCELLED"},
        "KNOWLEDGE_FAILED": {"KNOWLEDGE_BUILDING", "CANCELLED"},
        "SOW_GENERATION_FAILED": {"SOW_GENERATING", "CANCELLED"},
    },
    "workflow": {
        "EXTRACTION_FAILED": {"ISATVON_EXTRACTING", "CANCELLED"},
        "NEEDS_FDE_REVIEW": {"BUILDING", "CANCELLED"},
        "WORKFLOW_VALIDATION_FAILED": {"BUILDING", "CANCELLED"},
    },
    "document_version": {
        "SCAN_FAILED": {"SCANNING", "DELETED"},
        "PARSE_FAILED": {"SCANNING", "DELETED"},
        "REJECTED": {"DELETED"},
    },
    "sow_version": {"GENERATION_FAILED": {"GENERATED"}},
    "sub_skill": {
        "NEEDS_FDE_REVIEW": {"BUILDING"},
        "TESTING_BLOCKED": {"TESTING"},
        "DEPENDENCY_FAILED": {"BUILDING"},
        "BUILD_ERROR": {"BUILDING"},
    },
}


@pytest.mark.parametrize("name", list(FAILURE_EXITS))
def test_e0_6_failure_states_never_advance(name: str) -> None:
    m = MACHINES[MachineName(name)]
    for state, allowed in FAILURE_EXITS[name].items():
        targets = {r.to_state for r in m.transitions if r.from_state == state}
        assert targets <= allowed, (name, state, targets - allowed)


def test_e0_6_frozen_and_approved_states_only_leave_through_version_events() -> None:
    version_events = {
        "freeze",
        "start_patch_version",
        "scope_changed",
        "superseded",
        "sub_skill_invalidated",
        "sub_skill_patched",
    }
    for m in MACHINES.values():
        for state in m.locked:
            events = {r.event for r in m.transitions if r.from_state == state}
            assert "edit" not in events, (m.name, state)
            assert events <= version_events, (m.name, state, events - version_events)


@pytest.mark.parametrize(
    ("m", "row"), ALL_ROWS, ids=[f"{m.name}:{r.from_state}:{r.event}" for m, r in ALL_ROWS]
)
def test_e0_6_actor_must_match(m: Machine, row: Transition) -> None:
    for actor in Actor:
        if actor != row.actor:
            assert (
                code_of(m.name, row.from_state, row.event, actor)
                == InvalidTransitionCode.FORBIDDEN_ACTOR
            )


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("billing", "IDLE", "start", "FDE"), InvalidTransitionCode.UNKNOWN_MACHINE),
        (("workflow", "DONE", "freeze", "SYSTEM"), InvalidTransitionCode.UNKNOWN_STATE),
        (("workflow", "CERTIFIED", "teleport", "SYSTEM"), InvalidTransitionCode.UNKNOWN_EVENT),
        (("workflow", "FROZEN", "edit", "FDE"), InvalidTransitionCode.UNKNOWN_EVENT),
        (("sub_skill", "FROZEN", "edit", "FDE"), InvalidTransitionCode.LOCKED),
    ],
)
def test_e0_6_unknown_inputs_are_errors(args: tuple[str, ...], code: InvalidTransitionCode) -> None:
    assert code_of(*args) == code


# --- T07: reachability ----------------------------------------------------------------------------


@pytest.mark.parametrize("m", list(MACHINES.values()), ids=[m.value for m in MACHINES])
def test_e0_6_t07_every_state_reachable_and_only_terminals_are_dead_ends(m: Machine) -> None:
    edges: dict[str | None, set[str]] = {}
    for r in m.transitions:
        edges.setdefault(r.from_state, set()).add(r.to_state)
    seen: set[str] = set()
    queue = deque(edges[None])
    while queue:
        state = queue.popleft()
        if state not in seen:
            seen.add(state)
            queue.extend(edges.get(state, set()))
    assert seen == set(m.state_values())
    dead_ends = {s for s in m.state_values() if not edges.get(s)}
    assert dead_ends == set(m.terminal)


# --- T08: export ----------------------------------------------------------------------------------


def test_e0_6_t08_export_is_deterministic_and_matches_the_committed_file() -> None:
    assert _json(export()) == _json(export())
    assert STATE_MACHINES_FILE.read_bytes() == _json(export()), "run `make contracts`"


def test_e0_6_t08_export_shape() -> None:
    tables = json.loads(STATE_MACHINES_FILE.read_text(encoding="utf-8"))
    assert list(tables) == [m.value for m in MACHINES]
    for name, table in tables.items():
        assert set(table) == {"states", "initial", "terminal", "locked", "transitions"}
        assert all(set(t) == {"from", "event", "to", "actor"} for t in table["transitions"]), name


# --- T09: manifest status mapping -----------------------------------------------------------------


@pytest.mark.parametrize("state", list(SubSkillState), ids=[s.value for s in SubSkillState])
def test_e0_6_t09_every_sub_skill_state_maps_to_a_manifest_status(state: SubSkillState) -> None:
    assert isinstance(manifest_status(state), ManifestStatus)


def test_e0_6_t09_mapping_matches_td_14_4() -> None:
    expected = {
        "DRAFT": "DRAFT", "VALID": "DRAFT", "INVALID": "DRAFT",
        "BUILDING": "VALIDATING", "CORRECTING": "VALIDATING", "TESTING": "VALIDATING",
        "CERTIFIED": "APPROVED", "FROZEN": "APPROVED", "SUPERSEDED": "SUPERSEDED",
    }  # fmt: skip
    assert {s: manifest_status(SubSkillState(s)).value for s in expected} == expected


# --- CHECK constraint values ----------------------------------------------------------------------


@pytest.mark.parametrize("name", list(MachineName), ids=[m.value for m in MachineName])
def test_e0_6_check_constraint_values_equal_the_enum(name: MachineName) -> None:
    assert check_constraint_values(name) == tuple(s.value for s in MACHINES[name].states)
