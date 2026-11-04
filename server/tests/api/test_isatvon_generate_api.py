"""#678 `POST W/generate` through the real app and auth chain: the Workbench's Generate tells the
pipeline waiting at WAIT_GENERATE to build. The Temporal client is the approval tests' fake: its
`status` query says where the pipeline is, and every signal is recorded."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.sow.models import SowVersion
from sbam_api.temporal.models import TemporalRun
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import Generate, PipelineStage
from sqlalchemy import select, update
from test_auth import ADMIN_A, FDE_A1, FDE_A2, FDE_B1, TENANT_A, TENANT_B, problem
from test_isatvon_export_api import Harness, sample


class GenerateHarness(Harness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        TemporalRun.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.pipeline.stage = PipelineStage.WAIT_GENERATE
        with self.sessions() as s:
            s.execute(update(SowVersion).where(SowVersion.id == self.v1).values(state="APPROVED"))
            self.run_id = uuid7()
            s.add(
                TemporalRun(
                    id=self.run_id,
                    tenant_id=TENANT_A,
                    project_id=uuid.UUID(self.project),
                    temporal_workflow_id=f"pipeline-{self.project}",
                    temporal_run_id="run-1",
                    type="ProjectPipeline",
                    task_queue="sbam-ingest",
                    subject_type="project",
                    subject_id=uuid.UUID(self.project),
                    state="RUNNING",
                    current_stage="wait_generate",
                )
            )
            s.commit()

    def generate(
        self,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        workflow: uuid.UUID | None = None,
        key: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": key} if key else {}
        return self.call(
            "POST",
            f"/projects/{self.project}/workflows/{workflow or self.wf}/generate",
            token or self.fde(),
            json=body if body is not None else {},
            headers=headers,
        )

    def requested(self) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == "GENERATE_REQUESTED")))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[GenerateHarness]:
    yield GenerateHarness(tmp_path)


def test_e10_1b_t01_a_valid_breakdown_signals_the_waiting_pipeline(h: GenerateHarness) -> None:
    macro = h.macro(1, "MS01_A")
    h.sub(macro, 1, sample("MS01.1_A", "A"))
    h.sub(macro, 2, sample("MS01.2_B", "B"))
    res = h.generate(key="generate-key-1")
    assert res.status_code == 202, res.text
    assert res.json()["run"]["id"] == str(h.run_id)
    [(name, payload)] = h.pipeline.signals
    assert name == "generate" and isinstance(payload, Generate)
    assert payload.sub_skill_ids == []  # all of them, all VALID
    assert payload.idempotency_key == "generate-key-1" and payload.actor_id == str(FDE_A1)
    [audit] = h.requested()
    assert audit.actor_id == str(FDE_A1) and audit.entity_id == str(h.wf)


def test_e10_1b_t01_named_sub_skills_are_passed_on(h: GenerateHarness) -> None:
    macro = h.macro(1, "MS01_A")
    one = h.sub(macro, 1, sample("MS01.1_A", "A"))
    h.sub(macro, 2, sample("MS01.2_B", "B"), state="INVALID")  # not asked for: no matter
    res = h.generate({"sub_skill_ids": [str(one.id)]})
    assert res.status_code == 202, res.text
    [(_, payload)] = h.pipeline.signals
    assert payload.sub_skill_ids == [one.id]


def test_e10_1b_t02_an_invalid_sub_skill_is_409_and_nothing_is_signalled(
    h: GenerateHarness,
) -> None:
    macro = h.macro(1, "MS01_A")
    h.sub(macro, 1, sample("MS01.1_A", "A"))
    h.sub(macro, 2, sample("MS01.2_B", "B"), state="INVALID")
    res = h.generate()
    problem(res, 409, "VALIDATION_ERROR")
    assert [e["path"] for e in res.json()["errors"]] == ["sub_skills.MS01.2_B"]
    assert h.pipeline.signals == [] and h.requested() == []


def test_e10_1b_t02_an_empty_breakdown_cannot_be_generated(h: GenerateHarness) -> None:
    problem(h.generate(), 409, "VALIDATION_ERROR")
    assert h.pipeline.signals == []


def test_e10_1b_t02_an_unapproved_sow_is_409(h: GenerateHarness) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    with h.sessions() as s:
        s.execute(update(SowVersion).where(SowVersion.id == h.v1).values(state="GENERATED"))
        s.commit()
    problem(h.generate(), 409, "SOW_NOT_APPROVED")
    assert h.pipeline.signals == []


@pytest.mark.parametrize(
    ("state", "status", "code"),
    [("BUILDING", 409, "WORKFLOW_BUILDING"), ("FROZEN", 423, "WORKFLOW_FROZEN")],
)
def test_e10_1b_t02_a_workflow_past_review_is_refused(
    h: GenerateHarness, state: str, status: int, code: str
) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    with h.sessions() as s:
        s.execute(update(Workflow).where(Workflow.id == h.wf).values(state=state))
        s.commit()
    problem(h.generate(), status, code)
    assert h.pipeline.signals == []


def test_e10_1b_t03_a_pipeline_not_waiting_is_409(h: GenerateHarness) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    h.pipeline.stage = PipelineStage.BUILD_SUB_SKILLS
    problem(h.generate(), 409, "PIPELINE_NOT_WAITING")
    assert h.pipeline.signals == [] and h.requested() == []


def test_e10_1b_t03_temporal_down_is_503(h: GenerateHarness) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    h.pipeline.down = True
    problem(h.generate(), 503, "TEMPORAL_UNAVAILABLE")
    assert h.requested() == []


def test_e10_1b_t04_a_repeated_key_answers_the_same_run_with_one_signal(
    h: GenerateHarness,
) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    first = h.generate(key="generate-same")
    again = h.generate(key="generate-same")
    assert first.status_code == again.status_code == 202
    assert first.json()["run"]["id"] == again.json()["run"]["id"]
    assert len(h.pipeline.signals) == 1 and len(h.requested()) == 1


def test_e10_1b_t05_isolation(h: GenerateHarness) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    problem(h.generate(token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")  # unassigned
    problem(h.generate(token=h.realm.token(ADMIN_A)), 403, "WRONG_PORTAL")
    other = h.realm.token(FDE_B1, tenant_id=[str(TENANT_B)])
    problem(h.generate(token=other), 404, "NOT_FOUND")  # another tenant
    assert h.pipeline.signals == []


def test_e10_1b_t06_a_sub_skill_of_another_workflow_is_404(h: GenerateHarness) -> None:
    h.sub(h.macro(1, "MS01_A"), 1, sample("MS01.1_A", "A"))
    problem(h.generate({"sub_skill_ids": [str(uuid.uuid4())]}), 404, "NOT_FOUND")
    problem(h.generate(workflow=uuid.uuid4()), 404, "NOT_FOUND")
    assert h.pipeline.signals == []
