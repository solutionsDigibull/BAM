"""The build timeline (#926): `GET W/timeline`, read from what the build already wrote.

One build id (the Temporal workflow id `build-<sub_skill>-<build_no>`) ties together the audit
transitions, the `llm_calls` rows, the `code_attempts` and the `gate_results` of a Sub-Skill's
newest build. Nothing is stored for it. A Workflow or project outside this tenant and the caller's
assignments is a 404 (the repositories scope every query); the response never carries a message,
a model output or a document content, only names, codes, counts, tokens and money.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sbam_contracts.build_dashboard import GateName
from sbam_contracts.build_timeline import (
    BuildTimelineResponse,
    StepKind,
    StepStatus,
    SubSkillTimeline,
    TimelineFailure,
    TimelineFinding,
    TimelineStep,
)
from sbam_contracts.runs import SubjectType
from sbam_contracts.state_machines import MachineName, SubSkillState, WorkflowState
from sqlalchemy.orm import Session

from sbam_api.audit.repository import entity_history
from sbam_api.builds.models import CodeAttempt, CodeAttemptStatus
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import not_found
from sbam_api.db.repository import TenantContext, TenantScopedRepository
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.llm.repository import LlmCallRepository
from sbam_api.manifests.models import GateResultRow
from sbam_api.temporal.models import TemporalRun
from sbam_api.temporal.repository import TemporalRunRepository
from sbam_api.workflows.repository import WorkflowRepository

PARKED = frozenset(
    {
        SubSkillState.NEEDS_FDE_REVIEW,
        SubSkillState.DEPENDENCY_FAILED,
        SubSkillState.TESTING_BLOCKED,
        SubSkillState.BUILD_ERROR,
    }
)
ACTIVE = frozenset({SubSkillState.BUILDING, SubSkillState.CORRECTING, SubSkillState.TESTING})
MAX_FINDINGS = 50
BUILD_PREFIX = "build-"
ZERO = "0.0000"
_STATES = {s.value for s in SubSkillState}
_FAILED_ATTEMPT = (CodeAttemptStatus.PRECHECK_FAILED, CodeAttemptStatus.ASSEMBLY_FAILED)


class _Attempts(TenantScopedRepository[CodeAttempt]):
    model = CodeAttempt

    def of_build(self, sub_skill_id: uuid.UUID) -> list[CodeAttempt]:
        query = self._scoped().where(CodeAttempt.sub_skill_id == sub_skill_id)
        return list(self.session.scalars(query.order_by(CodeAttempt.id)))


class _Gates(TenantScopedRepository[GateResultRow]):
    model = GateResultRow

    def of_build(self, sub_skill_id: uuid.UUID, build_no: int) -> list[GateResultRow]:
        query = self._scoped().where(
            GateResultRow.sub_skill_id == sub_skill_id, GateResultRow.build_no == build_no
        )
        return list(self.session.scalars(query.order_by(GateResultRow.id)))


def money(value: Decimal | int) -> str:
    return f"{Decimal(value):.4f}"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _findings(gate: GateName, raw: list[dict[str, Any]] | None) -> list[TimelineFinding]:
    """Names only: the gate, the rule code and the position. The message text is never copied."""
    out: list[TimelineFinding] = []
    for item in raw or []:
        if len(out) >= MAX_FINDINGS:
            break
        line = item.get("line")
        out.append(
            TimelineFinding(
                gate=gate,
                code=str(item.get("code") or "UNKNOWN")[:100],
                file=str(item["file"])[:500] if item.get("file") else None,
                line=line if isinstance(line, int) and line >= 1 else None,
                package=str(item["package"])[:200] if item.get("package") else None,
            )
        )
    return out


def _build_no(build_id: str | None) -> int | None:
    tail = (build_id or "").rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() and int(tail) >= 1 else None


def _state(value: Any) -> SubSkillState | None:
    return SubSkillState(value) if value in _STATES else None


def timeline(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID
) -> BuildTimelineResponse:
    ctx = user.context(project_id)
    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    subs = SubSkillRepository(session, ctx).of_workflow(workflow_id)
    runs = TemporalRunRepository(session, ctx).latest_for_subjects(
        SubjectType.SUB_SKILL.value, [s.id for s in subs]
    )
    rows = [_sub_timeline(session, ctx, sub, runs.get(sub.id)) for sub in subs]
    return BuildTimelineResponse(
        workflow_id=workflow_id,
        workflow_state=WorkflowState(workflow.state),
        cost_usd=money(sum((Decimal(r.cost_usd) for r in rows), Decimal(0))),
        sub_skills=rows,
    )


def _step(
    at: datetime, kind: StepKind, name: str, status: StepStatus, **more: Any
) -> dict[str, Any]:
    return {"started_at": at, "kind": kind, "name": name[:255], "status": status, **more}


def _sub_timeline(
    session: Session, ctx: TenantContext, sub: SubSkill, run: TemporalRun | None
) -> SubSkillTimeline:
    state = SubSkillState(sub.state)
    all_attempts = _Attempts(session, ctx).of_build(sub.id)
    build_id = run.temporal_workflow_id if run is not None else None
    build_no = _build_no(build_id) if build_id and build_id.startswith(BUILD_PREFIX) else None
    if build_no is None:
        build_no = max((a.build_no for a in all_attempts), default=None)
        build_id = f"{BUILD_PREFIX}{sub.id}-{build_no}" if build_no else None
    attempts = [a for a in all_attempts if a.build_no == build_no]
    gates = _Gates(session, ctx).of_build(sub.id, build_no) if build_no else []
    calls = LlmCallRepository(session, ctx).of_build(build_id) if build_id else []
    began = _aware(run.created_at) if run is not None else None

    raw: list[dict[str, Any]] = []
    by_activity: dict[str, list[Any]] = {}
    for call in calls:
        by_activity.setdefault(call.activity_id, []).append(call)
    for activity, group in by_activity.items():
        raw.append(
            _step(
                _aware(group[0].created_at),
                StepKind.MODEL,
                activity,
                StepStatus.FAILED if group[-1].status != "SUCCEEDED" else StepStatus.OK,
                alias=group[0].alias,
                calls=len(group),
                tokens_in=sum(c.tokens_in for c in group),
                tokens_out=sum(c.tokens_out for c in group),
                cost_usd=money(sum((Decimal(str(c.cost_usd)) for c in group), Decimal(0))),
            )
        )
    for att in attempts:
        failed = att.status in _FAILED_ATTEMPT
        raw.append(
            _step(
                _aware(att.created_at),
                StepKind.CODE_ATTEMPT,
                att.status,
                StepStatus.FAILED if failed else StepStatus.OK,
                cycle=att.cycle,
                attempt=att.attempt,
                findings=_findings(GateName.CODEGEN, att.findings) if failed else [],
            )
        )
    for gate in gates:
        if gate.status == "FAILED":  # a passed gate is the absence of a failure (the grid shows it)
            raw.append(
                _step(
                    _aware(gate.created_at),
                    StepKind.GATE,
                    gate.gate,
                    StepStatus.FAILED,
                    cycle=gate.cycle,
                    attempt=gate.attempt,
                    findings=_findings(GateName(gate.gate), gate.findings),
                )
            )
    transitions = []
    for row in entity_history(session, ctx.tenant_id, MachineName.SUB_SKILL.value, str(sub.id)):
        at = _aware(row.at)
        if began is not None and at < began:
            continue
        to_state = _state((row.after or {}).get("state"))
        transitions.append((row, at, to_state))
        raw.append(
            _step(
                at,
                StepKind.TRANSITION,
                row.action,
                StepStatus.FAILED if to_state in PARKED else StepStatus.OK,
                from_state=_state((row.before or {}).get("state")),
                to_state=to_state,
            )
        )

    raw.sort(key=lambda step: step["started_at"])
    steps: list[TimelineStep] = []
    for i, step in enumerate(raw):
        nxt = raw[i + 1]["started_at"] if i + 1 < len(raw) else None
        duration = int((nxt - step["started_at"]).total_seconds() * 1000) if nxt else None
        if nxt is None and state in ACTIVE:
            step["status"] = StepStatus.RUNNING
        steps.append(TimelineStep(seq=i + 1, duration_ms=duration, **step))

    failure = None
    if state in PARKED and transitions:
        row, at, _ = transitions[-1]
        failed_gates = [g for g in gates if g.status == "FAILED"]
        newest = max(((g.cycle or 0, g.attempt or 0) for g in failed_gates), default=None)
        failing = [g for g in failed_gates if (g.cycle or 0, g.attempt or 0) == newest]
        found = [f for g in failing for f in _findings(GateName(g.gate), g.findings)]
        for att in [a for a in attempts if a.status == CodeAttemptStatus.PRECHECK_FAILED][-1:]:
            found += _findings(GateName.CODEGEN, att.findings)
        order = list(GateName)
        failure = TimelineFailure(
            event=row.action[:100],
            state=state,
            at=at,
            failing_gates=sorted({GateName(g.gate) for g in failing}, key=order.index),
            findings=found[:MAX_FINDINGS],
        )
    return SubSkillTimeline(
        sub_skill_id=sub.id,
        skill_id=sub.skill_id,
        skill_name=sub.skill_name,
        state=state,
        build_id=build_id,
        build_no=build_no,
        started_at=began or (raw[0]["started_at"] if raw else None),
        last_activity_at=max((s["started_at"] for s in raw), default=None),
        current_stage=run.current_stage if run is not None else None,
        code_attempts=len(attempts),
        llm_calls=len(calls),
        tokens_in=sum(c.tokens_in for c in calls),
        tokens_out=sum(c.tokens_out for c in calls),
        cost_usd=money(sum((Decimal(str(c.cost_usd)) for c in calls), Decimal(0))),
        failure=failure,
        steps=steps,
    )
