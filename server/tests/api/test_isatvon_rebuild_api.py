"""#695 `POST W/rebuild` through the real app and auth chain: the FDE rebuilds Sub-Skills that need
review, telling the pipeline waiting at WAIT_REBUILD to go on. Same fake Temporal client as the
generate tests: its `status` query says where the pipeline is, and every signal is recorded."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.isatvon.models import SubSkill
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import PipelineStage, RebuildSubSkills
from sqlalchemy import select, update
from test_auth import ADMIN_A, FDE_A1, FDE_A2, FDE_B1, TENANT_B, problem
from test_isatvon_export_api import sample
from test_isatvon_generate_api import GenerateHarness


class RebuildHarness(GenerateHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.pipeline.stage = PipelineStage.WAIT_REBUILD
        self.set_workflow("NEEDS_FDE_REVIEW")

    def set_workflow(self, state: str) -> None:
        with self.sessions() as s:
            s.execute(update(Workflow).where(Workflow.id == self.wf).values(state=state))
            s.commit()

    def post_rebuild(
        self,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        workflow: uuid.UUID | None = None,
        key: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": key} if key else {}
        return self.call(
            "POST",
            f"/projects/{self.project}/workflows/{workflow or self.wf}/rebuild",
            token or self.fde(),
            json=body if body is not None else {},
            headers=headers,
        )

    def retried(self) -> list[AuditLog]:
        with self.sessions() as s:
            rows = s.scalars(select(AuditLog).where(AuditLog.entity_type == "sub_skill"))
            return [r for r in rows if (r.after or {}).get("state") == "BUILDING"]

    def rebuilds(self) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == "REBUILD_REQUESTED")))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[RebuildHarness]:
    yield RebuildHarness(tmp_path)


def _two(h: RebuildHarness) -> tuple[SubSkill, SubSkill, SubSkill]:
    macro = h.macro(1, "MS01_A")
    a = h.sub(macro, 1, sample("MS01.1_A", "A"), state="NEEDS_FDE_REVIEW")
    b = h.sub(macro, 2, sample("MS01.2_B", "B"), state="DEPENDENCY_FAILED")
    c = h.sub(macro, 3, sample("MS01.3_C", "C"), state="CERTIFIED")
    return a, b, c


def test_695_t01_rebuild_signals_once_and_moves_the_sub_skills(h: RebuildHarness) -> None:
    a, _, _ = _two(h)
    res = h.post_rebuild({"sub_skill_ids": [str(a.id)]}, key="rebuild-key-1")
    assert res.status_code == 202, res.text
    assert res.json()["run"]["id"] == str(h.run_id)
    [(name, payload)] = h.pipeline.signals
    assert name == "rebuild_subskills" and isinstance(payload, RebuildSubSkills)
    assert payload.sub_skill_ids == [a.id]
    assert payload.idempotency_key == "rebuild-key-1" and payload.actor_id == str(FDE_A1)
    assert h.states() == {
        "MS01.1_A": "BUILDING",
        "MS01.2_B": "DEPENDENCY_FAILED",
        "MS01.3_C": "CERTIFIED",
    }
    [moved] = h.retried()
    assert moved.entity_id == str(a.id) and moved.actor_id == str(FDE_A1)
    [audit] = h.rebuilds()
    assert audit.actor_id == str(FDE_A1) and audit.entity_id == str(h.wf)


def test_695_t02_no_ids_means_every_sub_skill_that_needs_review(h: RebuildHarness) -> None:
    a, b, _ = _two(h)
    assert h.post_rebuild().status_code == 202
    [(_, payload)] = h.pipeline.signals
    assert set(payload.sub_skill_ids) == {a.id, b.id}
    assert h.states() == {"MS01.1_A": "BUILDING", "MS01.2_B": "BUILDING", "MS01.3_C": "CERTIFIED"}


def test_695_t03_a_repeated_key_signals_and_moves_once(h: RebuildHarness) -> None:
    _two(h)
    first = h.post_rebuild(key="rebuild-same")
    again = h.post_rebuild(key="rebuild-same")
    assert first.status_code == again.status_code == 202
    assert first.json()["run"]["id"] == again.json()["run"]["id"]
    assert len(h.pipeline.signals) == 1 and len(h.rebuilds()) == 1 and len(h.retried()) == 2


def test_695_t04_a_sub_skill_not_needing_review_is_409(h: RebuildHarness) -> None:
    a, _, c = _two(h)
    res = h.post_rebuild({"sub_skill_ids": [str(a.id), str(c.id)]})
    problem(res, 409, "VALIDATION_ERROR")
    assert [e["path"] for e in res.json()["errors"]] == ["sub_skills.MS01.3_C"]
    assert h.pipeline.signals == [] and h.rebuilds() == [] and h.retried() == []


def test_695_t04_nothing_to_rebuild_is_409(h: RebuildHarness) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"), state="CERTIFIED")
    problem(h.post_rebuild(), 409, "VALIDATION_ERROR")
    assert h.pipeline.signals == []


@pytest.mark.parametrize(
    ("state", "status", "code"),
    [
        ("BUILDING", 409, "CONFLICT"),
        ("ISATVON_REVIEW", 409, "CONFLICT"),
        ("FROZEN", 423, "WORKFLOW_FROZEN"),
    ],
)
def test_695_t05_a_workflow_not_in_review_is_refused(
    h: RebuildHarness, state: str, status: int, code: str
) -> None:
    _two(h)
    h.set_workflow(state)
    problem(h.post_rebuild(), status, code)
    assert h.pipeline.signals == [] and h.retried() == []


@pytest.mark.parametrize("refusal", ["not_waiting", "temporal_down"])
def test_695_t06_a_refused_signal_leaves_the_sub_skills_and_audit_alone(
    h: RebuildHarness, refusal: str
) -> None:
    _two(h)
    if refusal == "not_waiting":
        h.pipeline.stage = PipelineStage.BUILD_SUB_SKILLS
    else:
        h.pipeline.down = True
    res = h.post_rebuild()
    if refusal == "not_waiting":
        problem(res, 409, "PIPELINE_NOT_WAITING")
    else:
        problem(res, 503, "TEMPORAL_UNAVAILABLE")
    assert h.states()["MS01.1_A"] == "NEEDS_FDE_REVIEW" and h.states()["MS01.2_B"] == (
        "DEPENDENCY_FAILED"
    )
    assert h.retried() == [] and h.rebuilds() == [] and h.pipeline.signals == []


def test_695_t07_isolation(h: RebuildHarness) -> None:
    _two(h)
    problem(h.post_rebuild(token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")  # unassigned
    problem(h.post_rebuild(token=h.realm.token(ADMIN_A)), 403, "WRONG_PORTAL")
    other = h.realm.token(FDE_B1, tenant_id=[str(TENANT_B)])
    problem(h.post_rebuild(token=other), 404, "NOT_FOUND")  # another tenant
    assert h.pipeline.signals == [] and h.retried() == []


def test_695_t08_a_foreign_sub_skill_or_workflow_is_404(h: RebuildHarness) -> None:
    _two(h)
    problem(h.post_rebuild({"sub_skill_ids": [str(uuid.uuid4())]}), 404, "NOT_FOUND")
    problem(h.post_rebuild(workflow=uuid.uuid4()), 404, "NOT_FOUND")
    assert h.pipeline.signals == [] and h.retried() == []
