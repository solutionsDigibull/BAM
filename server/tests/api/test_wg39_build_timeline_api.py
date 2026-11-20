"""WG-39 (#926) build timeline: `GET W/timeline`, read from the audit log, `llm_calls`,
`code_attempts` and `gate_results` of a Sub-Skill's newest build. The responses are parsed with the
committed `build_timeline` contract."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.llm.models import LlmCall
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.build_timeline import BuildTimelineResponse, StepKind, SubSkillTimeline
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_build_dashboard_api import Harness as DashHarness

T0 = datetime.now(UTC) - timedelta(hours=1)  # rows the build wrote "now" come after it
SECRET = "sk-or-" + "this-must-never-leave"  # built at run time


class Harness(DashHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        for table in (LlmCall.__table__, AuditLog.__table__):
            table.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]

    def build_id(self, build_no: int = 1, sub: uuid.UUID | None = None) -> str:
        return f"build-{sub or self.sub}-{build_no}"

    def put_llm(
        self,
        activity: str,
        minute: int,
        *,
        cost: str = "0.010000",
        status: str = "SUCCEEDED",
        tokens: tuple[int, int] = (100, 20),
        build_no: int = 1,
        tenant: uuid.UUID = TENANT_A,
    ) -> None:
        with self.sessions() as s:
            s.add(
                LlmCall(
                    id=uuid7(),
                    tenant_id=tenant,
                    project_id=uuid.UUID(self.project),
                    alias="sbam-codegen",
                    model="m",
                    prompt_id="p",
                    prompt_version="1",
                    prompt_sha256="0" * 64,
                    tokens_in=tokens[0],
                    tokens_out=tokens[1],
                    cost_usd=Decimal(cost),
                    latency_ms=5,
                    attempt=1,
                    status=status,
                    trace_id="tr",
                    context_manifest={},
                    correlation_id="c",
                    temporal_workflow_id=self.build_id(build_no),
                    activity_id=activity,
                    activity_attempt=1,
                    output={"text": SECRET} if status == "SUCCEEDED" else None,
                    error=SECRET if status != "SUCCEEDED" else None,
                    created_at=T0 + timedelta(minutes=minute),
                )
            )
            s.commit()

    def put_build_run(self, build_no: int, stage: str = "generate_bodies", minute: int = 0) -> None:
        """The run mirror of the build workflow, started at `T0 + minute`."""
        with self.sessions() as s:
            s.add(
                TemporalRun(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    temporal_workflow_id=self.build_id(build_no),
                    temporal_run_id=f"r{build_no}",
                    type="SubSkillBuild",
                    task_queue="build",
                    subject_type="sub_skill",
                    subject_id=self.sub,
                    state="RUNNING",
                    current_stage=stage,
                    created_at=T0 + timedelta(minutes=minute),
                )
            )
            s.commit()

    def put_audit(self, event: str, before: str, after: str, minute: int) -> None:
        with self.sessions() as s:
            s.add(
                AuditLog(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    actor_id="SYSTEM",
                    action=event,
                    entity_type="sub_skill",
                    entity_id=str(self.sub),
                    before={"state": before},
                    after={"state": after},
                    correlation_id="c",
                    at=T0 + timedelta(minutes=minute),
                )
            )
            s.commit()

    def timeline(self, token: str | None = None, workflow: uuid.UUID | None = None) -> Any:
        url = f"{self.path(workflow or self.workflow_id)}/timeline"
        return self.call("GET", url, token or self.fde())

    def one(self) -> SubSkillTimeline:
        [row] = BuildTimelineResponse.model_validate(self.timeline().json()).sub_skills
        return row


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def parked_build(h: Harness) -> None:
    h.put_state("NEEDS_FDE_REVIEW")
    h.put_build_run(1)
    h.put_audit("start_build", "VALID", "BUILDING", 0)
    h.put_llm("generate_bodies", 2)
    h.put_llm("generate_bodies", 3, status="SCHEMA_INVALID", cost="0.020000")
    h.attempt(
        0,
        None,
        status="PRECHECK_FAILED",
        findings=[{"code": "STATE_QUERY_INVALID", "message": SECRET, "file": "a.py", "line": 3}],
    )
    h.put_gate(
        "AST_IMPORTS",
        "FAILED",
        findings=[{"code": "IMPORT_NOT_ALLOWED", "message": SECRET, "file": "b.py", "line": 1}],
    )
    h.put_audit("generation_failed", "BUILDING", "NEEDS_FDE_REVIEW", 5)


def test_wg39_t01_a_sub_skill_without_a_build_has_an_empty_timeline(h: Harness) -> None:
    row = h.one()
    assert (row.build_id, row.steps, row.failure, row.cost_usd) == (None, [], None, "0.0000")


def test_wg39_t02_a_parked_build_is_explained_by_its_timeline_alone(h: Harness) -> None:
    parked_build(h)
    row = h.one()
    assert row.build_id == h.build_id() and row.build_no == 1
    assert row.failure is not None and row.failure.event == "generation_failed"
    assert [g.value for g in row.failure.failing_gates] == ["AST_IMPORTS"]
    codes = {f.code for f in row.failure.findings}
    assert codes == {"IMPORT_NOT_ALLOWED", "STATE_QUERY_INVALID"}
    kinds = [s.kind for s in row.steps]
    assert kinds[0] is StepKind.TRANSITION and StepKind.GATE in kinds
    assert [s.seq for s in row.steps] == list(range(1, len(row.steps) + 1))


def test_wg39_t03_tokens_cost_and_attempts_add_up_across_calls(h: Harness) -> None:
    parked_build(h)
    row = h.one()
    assert (row.llm_calls, row.tokens_in, row.tokens_out) == (2, 200, 40)
    assert row.cost_usd == "0.0300" and row.code_attempts == 1
    [model] = [s for s in row.steps if s.kind is StepKind.MODEL]
    assert (model.calls, model.cost_usd, model.status.value) == (2, "0.0300", "FAILED")
    assert BuildTimelineResponse.model_validate(h.timeline().json()).cost_usd == "0.0300"


def test_wg39_t04_steps_are_ordered_and_durations_run_to_the_next_step(h: Harness) -> None:
    parked_build(h)
    steps = h.one().steps
    starts = [s.started_at for s in steps]
    assert starts == sorted(starts)
    assert steps[0].duration_ms == 2 * 60_000 and steps[-1].duration_ms is None


def test_wg39_t05_no_secret_or_message_or_model_output_is_returned(h: Harness) -> None:
    parked_build(h)
    assert SECRET not in h.timeline().text


def test_wg39_t06_an_active_build_ends_in_a_running_step(h: Harness) -> None:
    h.put_state("BUILDING")
    h.put_build_run(1)
    h.put_audit("start_build", "VALID", "BUILDING", 0)
    h.put_llm("generate_bodies", 1)
    row = h.one()
    assert row.failure is None and row.steps[-1].status.value == "RUNNING"
    assert row.last_activity_at == T0 + timedelta(minutes=1)


def test_wg39_t07_only_the_newest_build_counts(h: Harness) -> None:
    h.put_llm("generate_bodies", 1, build_no=1, cost="5.000000")
    h.put_llm("generate_bodies", 9, build_no=2, cost="0.010000")
    h.put_build_run(1)
    h.put_build_run(2, minute=8)  # the newest run is the highest id
    row = h.one()
    assert (row.build_no, row.cost_usd) == (2, "0.0100")


def test_wg39_t08_cross_tenant_and_unassigned_are_404(h: Harness) -> None:
    parked_build(h)
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other, h.realm.token(FDE_A2)):
        problem(h.timeline(token), 404, "NOT_FOUND")
    problem(h.timeline(workflow=uuid.uuid4()), 404, "NOT_FOUND")


def test_wg39_t09_a_request_without_a_valid_token_is_refused(h: Harness) -> None:
    assert h.timeline("bad").status_code == 401
