"""E12.6b Build & Test dashboard API: `GET W/builds` and `GET W/sub-skills/{id}/gates`, over rows
the build wrote. The shapes are the committed `build_dashboard` contract, which the responses are
parsed with here, so a drift between what the client reads and what is served fails this test."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from sbam_api.core.ids import uuid7
from sbam_api.isatvon.models import SubSkill
from sbam_api.manifests.models import GateResultRow
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.build_dashboard import BuildsResponse, GateName, GatesResponse
from test_auth import FDE_A2, TENANT_A, TENANT_B, problem
from test_code_view_api import Harness as CodeHarness


class Harness(CodeHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        for table in (GateResultRow.__table__, TemporalRun.__table__):
            table.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]

    def put_state(self, state: str, sub: uuid.UUID | None = None) -> None:
        with self.sessions() as s:
            row = s.get(SubSkill, sub or self.sub)
            assert row is not None
            row.state = state
            s.commit()

    def put_gate(
        self,
        gate: str,
        status: str,
        *,
        build_no: int = 1,
        cycle: int = 0,
        attempt: int = 0,
        findings: list[dict[str, Any]] | None = None,
        sub: uuid.UUID | None = None,
    ) -> None:
        with self.sessions() as s:
            s.add(
                GateResultRow(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    sub_skill_id=sub or self.sub,
                    gate=gate,
                    status=status,
                    findings=findings or [],
                    tool_version="1.0",
                    build_no=build_no,
                    cycle=cycle,
                    attempt=attempt,
                )
            )
            s.commit()

    def put_stage(self, stage: str) -> None:
        with self.sessions() as s:
            s.add(
                TemporalRun(
                    id=uuid7(),
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    temporal_workflow_id="sub-skill-build",
                    temporal_run_id="r1",
                    type="SubSkillBuild",
                    task_queue="build",
                    subject_type="sub_skill",
                    subject_id=self.sub,
                    state="RUNNING",
                    current_stage=stage,
                )
            )
            s.commit()

    def builds(self, token: str | None = None, workflow: uuid.UUID | None = None) -> Any:
        return self.call(
            "GET", f"{self.path(workflow or self.workflow_id)}/builds", token or self.fde()
        )

    def gates(self, query: str = "", token: str | None = None, sub: uuid.UUID | None = None) -> Any:
        url = f"{self.path(self.workflow_id)}/sub-skills/{sub or self.sub}/gates{query}"
        return self.call("GET", url, token or self.fde())


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_e12_6b_t01_a_sub_skill_without_results_shows_no_gate_and_is_not_active(h: Harness) -> None:
    body = BuildsResponse.model_validate(h.builds().json())
    [row] = body.sub_skills
    assert (row.sub_skill_id, row.state.value, row.attempt, row.gates) == (h.sub, "VALID", 0, {})
    assert row.stage.value == "build_manifest" and body.active is False
    assert body.workflow_id == h.workflow_id


def test_e12_6b_t02_the_newest_attempt_of_the_newest_build_is_the_row(h: Harness) -> None:
    h.put_gate("AST_SYNTAX", "FAILED", attempt=0)
    h.put_gate("AST_SYNTAX", "PASSED", attempt=1)
    h.put_gate("RUFF", "PASSED", attempt=1)
    h.put_gate("AST_SYNTAX", "PASSED", build_no=1, cycle=0, attempt=2)
    h.put_gate(
        "AST_SYNTAX", "FAILED", build_no=2, cycle=0, attempt=0
    )  # a newer build wins over all
    [row] = BuildsResponse.model_validate(h.builds().json()).sub_skills
    assert row.attempt == 0
    assert {g.value: s.value for g, s in row.gates.items()} == {"AST_SYNTAX": "FAILED"}


def test_e12_6b_t03_a_retry_cycle_replaces_the_failed_one(h: Harness) -> None:
    h.put_gate("MYPY", "FAILED", cycle=0, attempt=2)
    h.put_gate("MYPY", "PASSED", cycle=1, attempt=0)
    [row] = BuildsResponse.model_validate(h.builds().json()).sub_skills
    assert (row.attempt, row.gates[GateName.MYPY].value) == (0, "PASSED")


def test_e12_6b_t04_a_building_sub_skill_is_active_and_its_stage_gate_runs(h: Harness) -> None:
    h.put_state("BUILDING")
    h.put_stage("check_libraries")
    body = BuildsResponse.model_validate(h.builds().json())
    assert body.active is True
    assert body.sub_skills[0].stage.value == "check_libraries"
    assert body.sub_skills[0].gates[GateName.LIBRARIES].value == "RUNNING"


@pytest.mark.parametrize(
    ("state", "stage"),
    [("CERTIFIED", "done"), ("FROZEN", "done"), ("NEEDS_FDE_REVIEW", "wait_retry")],
)
def test_e12_6b_t05_a_finished_or_waiting_sub_skill_is_not_active(
    h: Harness, state: str, stage: str
) -> None:
    h.put_state(state)
    h.put_stage("ast_gate")  # a stale mirror never overrides the Sub-Skill's own state
    body = BuildsResponse.model_validate(h.builds().json())
    assert body.active is False and body.sub_skills[0].stage.value == stage


def test_e12_6b_t06_gates_come_in_order_with_pending_for_the_ones_not_run(h: Harness) -> None:
    finding = {"gate": "RUFF", "code": "F401", "message": "unused", "file": "a.py", "line": 3}
    h.put_gate("RUFF", "FAILED", findings=[finding, {"message": "old shape", "extra": 1}])
    body = GatesResponse.model_validate(h.gates().json())
    assert [g.gate for g in body.gates] == list(GateName)
    ruff = next(g for g in body.gates if g.gate == GateName.RUFF)
    assert ruff.status.value == "FAILED" and ruff.tool_version == "1.0"
    assert [(f.code, f.line) for f in ruff.findings] == [("F401", 3), ("UNKNOWN", None)]
    assert {g.status.value for g in body.gates if g.gate != GateName.RUFF} == {"PENDING"}
    assert body.attempt == 0


def test_e12_6b_t07_the_attempt_query_picks_an_attempt_and_defaults_to_the_newest(
    h: Harness,
) -> None:
    h.put_gate("BLACK", "FAILED", attempt=0)
    h.put_gate("BLACK", "PASSED", attempt=1)
    newest = GatesResponse.model_validate(h.gates().json())
    first = GatesResponse.model_validate(h.gates("?attempt=0").json())
    assert newest.attempt == 1 and first.attempt == 0
    assert [
        next(g for g in r.gates if g.gate == GateName.BLACK).status.value for r in (newest, first)
    ] == ["PASSED", "FAILED"]
    assert h.gates("?attempt=3").status_code == 422
    assert h.gates("?attempt=-1").status_code == 422


def test_e12_6b_t08_no_results_at_all_is_every_gate_pending_not_an_error(h: Harness) -> None:
    body = GatesResponse.model_validate(h.gates().json())
    assert len(body.gates) == len(GateName) and body.attempt == 0
    assert {g.status.value for g in body.gates} == {"PENDING"}


def test_e12_6b_t09_other_tenants_unassigned_fdes_and_invented_ids_get_404(h: Harness) -> None:
    h.put_gate("RUFF", "PASSED")
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    unassigned = h.realm.token(FDE_A2)
    for token in (other, unassigned):
        problem(h.builds(token), 404, "NOT_FOUND")
        problem(h.gates(token=token), 404, "NOT_FOUND")
    problem(h.builds(workflow=uuid7()), 404, "NOT_FOUND")
    problem(h.gates(sub=uuid7()), 404, "NOT_FOUND")


def test_e12_6b_t10_a_sub_skill_of_another_workflow_or_tenant_is_404(h: Harness) -> None:
    other_workflow = h.workflow(2, sow_version=h.generated(h.structured))
    foreign = h.sub_skill(other_workflow)
    h.put_gate("RUFF", "PASSED", sub=foreign)
    problem(h.gates(sub=foreign), 404, "NOT_FOUND")
    elsewhere = h.sub_skill(h.workflow_id, tenant=TENANT_B)
    problem(h.gates(sub=elsewhere), 404, "NOT_FOUND")
    other_rows = BuildsResponse.model_validate(h.builds().json()).sub_skills
    assert [r.sub_skill_id for r in other_rows] == [h.sub]  # neither foreign one is listed


def test_e12_6b_t11_a_workflow_of_another_project_is_404(h: Harness) -> None:
    from sbam_api.workflows.models import Workflow

    stranger = h.workflow(2, sow_version=h.generated(h.structured))
    with h.sessions() as s:
        row = s.get(Workflow, stranger)
        assert row is not None
        row.project_id = uuid.uuid4()
        s.commit()
    problem(h.builds(workflow=stranger), 404, "NOT_FOUND")


def test_e12_6b_t12_without_a_token_it_is_401(h: Harness) -> None:
    url = f"{h.path(h.workflow_id)}/builds"
    res = h.client.get(f"/api/v1{url}" if not url.startswith("/api") else url)
    assert res.status_code == 401
