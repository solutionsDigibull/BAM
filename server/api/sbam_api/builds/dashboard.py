"""Build & Test dashboard reads (E12.6b, TD §8.10, §9.3): every Sub-Skill's gates, and one gate
set's findings. Everything comes from what the build wrote (`gate_results`, the Sub-Skill's state,
the run mirror's stage); the shapes are `sbam_contracts.build_dashboard`.

A Workflow, Sub-Skill, or project outside this tenant and the caller's assignments is a 404, as the
repositories scope every query. A Sub-Skill that has no gate result yet shows every gate PENDING.
"""

import uuid
from typing import Any

from pydantic import ValidationError
from sbam_contracts.build import BuildStage
from sbam_contracts.build_dashboard import (
    BuildRow,
    BuildsResponse,
    Finding,
    GateName,
    GateResult,
    GatesResponse,
    GateStatus,
)
from sbam_contracts.runs import SubjectType
from sbam_contracts.state_machines import SubSkillState, WorkflowState
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import not_found
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.manifests.models import GateResultRow
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.temporal.repository import TemporalRunRepository
from sbam_api.workflows.repository import WorkflowRepository

ACTIVE = frozenset({SubSkillState.BUILDING, SubSkillState.CORRECTING, SubSkillState.TESTING})
DONE = frozenset({SubSkillState.CERTIFIED, SubSkillState.FROZEN})
WAITING = frozenset(
    {
        SubSkillState.NEEDS_FDE_REVIEW,
        SubSkillState.DEPENDENCY_FAILED,
        SubSkillState.TESTING_BLOCKED,
        SubSkillState.BUILD_ERROR,
    }
)
# The gate a stage is working on while it has no result yet.
RUNNING_GATE = {
    BuildStage.BUILD_MANIFEST: GateName.ISATVON_CONTRACT,
    BuildStage.CHECK_LIBRARIES: GateName.LIBRARIES,
    BuildStage.RENDER_SKELETON: GateName.CODEGEN,
    BuildStage.GENERATE_BODIES: GateName.CODEGEN,
    BuildStage.ASSEMBLE: GateName.CODEGEN,
    BuildStage.RUN_SANDBOX_TESTS: GateName.LOOP_TESTS,
}


def _stage(state: str, mirrored: str | None) -> BuildStage:
    if state in DONE:
        return BuildStage.DONE
    if state in WAITING:
        return BuildStage.WAIT_RETRY
    try:
        return BuildStage(mirrored) if mirrored else BuildStage.BUILD_MANIFEST
    except ValueError:  # a stage name this version does not know
        return BuildStage.BUILD_MANIFEST


def _finding(gate: str, raw: dict[str, Any]) -> Finding:
    known = {k: v for k, v in raw.items() if k in Finding.model_fields}
    try:
        return Finding.model_validate({**known, "gate": gate})
    except ValidationError:  # stored by an older worker: keep what is readable, never drop it
        return Finding(
            gate=GateName(gate),
            code=str(raw.get("code") or "UNKNOWN")[:100],
            message=str(raw.get("message", ""))[:10_000],
        )


def _result(row: GateResultRow) -> GateResult:
    return GateResult(
        gate=GateName(row.gate),
        status=GateStatus(row.status),
        findings=[_finding(row.gate, raw) for raw in row.findings or []],
        tool_version=row.tool_version,
    )


def builds(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID
) -> BuildsResponse:
    ctx = user.context(project_id)
    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    subs = SubSkillRepository(session, ctx).of_workflow(workflow_id)
    newest: dict[uuid.UUID, tuple[int, int, int]] = {}
    seen = GateResultRepository(session, ctx).stage_statuses([s.id for s in subs])
    for sub_id, build_no, cycle, attempt, _gate, _status in seen:
        key = (build_no, cycle, attempt)
        if key > newest.get(sub_id, (0, -1, -1)):
            newest[sub_id] = key
    gates: dict[uuid.UUID, dict[GateName, GateStatus]] = {s.id: {} for s in subs}
    for sub_id, build_no, cycle, attempt, gate, status in seen:
        if newest[sub_id] == (build_no, cycle, attempt):
            gates[sub_id][GateName(gate)] = GateStatus(status)
    runs = TemporalRunRepository(session, ctx)
    rows = [_row(s, newest.get(s.id), gates[s.id], runs) for s in subs]
    return BuildsResponse(
        workflow_id=workflow_id,
        workflow_state=WorkflowState(workflow.state),
        active=any(row.state in ACTIVE for row in rows),
        sub_skills=rows,
    )


def _row(
    sub: SubSkill,
    key: tuple[int, int, int] | None,
    gates: dict[GateName, GateStatus],
    runs: TemporalRunRepository,
) -> BuildRow:
    state = SubSkillState(sub.state)
    run = runs.latest_for_subject(SubjectType.SUB_SKILL.value, sub.id)
    stage = _stage(sub.state, run.current_stage if run else None)
    running = RUNNING_GATE.get(stage)
    if state in ACTIVE and running is not None and running not in gates:
        gates = {**gates, running: GateStatus.RUNNING}
    return BuildRow(
        sub_skill_id=sub.id,
        skill_id=sub.skill_id,
        skill_name=sub.skill_name,
        state=state,
        stage=stage,
        attempt=key[2] if key else 0,
        gates=gates,
    )


def gates(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID,
    attempt: int | None,
) -> GatesResponse:
    """One attempt's results in `GateName` order; a gate that has not run is PENDING. Without
    `attempt`, the newest attempt of the newest build."""
    ctx = user.context(project_id)
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    sub = SubSkillRepository(session, ctx).get(sub_skill_id)
    if sub is None or sub.workflow_id != workflow_id:  # own, foreign or invented id
        raise not_found()
    repo = GateResultRepository(session, ctx)
    keys = {(b, c, a) for _, b, c, a, _g, _s in repo.stage_statuses([sub_skill_id])}
    build_no, cycle = max(((b, c) for b, c, _ in keys), default=(0, 0))
    picked = (
        attempt
        if attempt is not None
        else max((a for b, c, a in keys if (b, c) == (build_no, cycle)), default=0)
    )
    done: dict[str, GateResultRow] = {}
    if keys:
        done = {r.gate: r for r in repo.of_stage(sub_skill_id, build_no, cycle, picked)}
    out = [
        _result(done[g.value]) if g.value in done else GateResult(gate=g, status=GateStatus.PENDING)
        for g in GateName
    ]
    return GatesResponse(sub_skill_id=sub_skill_id, attempt=picked, gates=out)
