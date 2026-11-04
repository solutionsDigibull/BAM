"""#694 POST P/pipeline/start after a schema failure: a pipeline that failed at build_schema_sql
(project WORKFLOW_CREATED, Workflow back in ISATVON_REVIEW) is restarted at the Generate wait.
Temporal is the approval tests' fake; its `status` query reports the failed stage."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import PipelineStage, PipelineStatus
from sqlalchemy import select, update
from test_auth import ADMIN_A, FDE_A2, FDE_B1, problem
from test_isatvon_export_api import sample
from test_isatvon_generate_api import GenerateHarness
from test_sow_approve_api import Handle, PipelineClient


class FailedHandle(Handle):
    async def query(self, name: str, **_: Any) -> PipelineStatus:
        client: Any = self.client
        return PipelineStatus(stage=client.stage, failed_stage=client.failed_stage)


class FailedPipeline(PipelineClient):
    def __init__(self) -> None:
        super().__init__()
        self.stage = PipelineStage.FAILED
        self.failed_stage: PipelineStage | None = PipelineStage.BUILD_SCHEMA_SQL
        self.open = False

    def get_workflow_handle(self, workflow_id: str, **_: Any) -> FailedHandle:
        return FailedHandle(self)


class Failed(GenerateHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.pipeline = FailedPipeline()
        self.client.app.state.temporal = self.pipeline  # type: ignore[attr-defined]
        self.set_state(self.project, "WORKFLOW_CREATED")
        macro = self.macro(1, "MS01_A")
        self.subs = [
            self.sub(macro, 1, sample("MS01.1_A", "A")).id,
            self.sub(macro, 2, sample("MS01.2_B", "B")).id,
        ]

    def start(self, token: str | None = None) -> Any:
        return self.call("POST", f"/projects/{self.project}/pipeline/start", token or self.fde())

    def project_state(self) -> str:
        return next(str(p.state) for p in self.projects() if str(p.id) == self.project)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Failed]:
    yield Failed(tmp_path)


def test_694_t04_start_after_a_schema_failure_resumes_at_wait_generate(h: Failed) -> None:
    res = h.start()
    assert res.status_code == 202, res.text
    [arg] = h.pipeline.started
    assert arg.resume is not None
    assert arg.resume.stage is PipelineStage.WAIT_GENERATE
    assert arg.resume.workflow_id == h.wf
    assert set(arg.resume.sub_skill_ids) == set(h.subs)
    assert h.project_state() == "WORKFLOW_CREATED"  # nothing moves: the Workflow is already back
    with h.sessions() as s:
        actions = [r.action for r in s.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert "PIPELINE_STARTED" in actions and "retry" not in actions


@pytest.mark.parametrize("failed", [PipelineStage.BUILD_SUB_SKILLS, PipelineStage.KNOWLEDGE, None])
def test_694_t05_a_failure_elsewhere_is_still_409(h: Failed, failed: PipelineStage | None) -> None:
    h.pipeline.failed_stage = failed
    problem(h.start(), 409, "INVALID_TRANSITION")
    assert h.pipeline.started == []


def test_694_t06_a_workflow_that_is_not_in_review_is_409(h: Failed) -> None:
    with h.sessions() as s:
        s.execute(update(Workflow).where(Workflow.id == h.wf).values(state="BUILDING"))
        s.commit()
    problem(h.start(), 409, "INVALID_TRANSITION")
    assert h.pipeline.started == []


def test_694_t07_start_is_still_tenant_and_role_scoped(h: Failed) -> None:
    problem(h.start(token=h.realm.token(FDE_A2)), 404, "NOT_FOUND")  # not assigned: no leak
    problem(h.start(token=h.realm.token(FDE_B1)), 404, "NOT_FOUND")  # another tenant
    assert h.start(token=h.realm.token(ADMIN_A)).status_code == 403
    assert h.pipeline.started == []
