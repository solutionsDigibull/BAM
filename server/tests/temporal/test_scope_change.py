"""E7.2 on Temporal's time-skipping server: a scope-change approval starts a ProjectPipeline run
that resumes at the approval with it (as `restart_pipeline` does); the real `create_workflow` and
`carry_over_workflow` make Workflow 2.0.0 from the previous version's rows. Extraction and the
rest are the usual stand-ins. The world is `scope_change_db`'s (real tables, SQLite)."""

import asyncio
import copy
import uuid
from typing import Any

from sbam_api.pipeline.service import start_pipeline
from sbam_api.projects.models import Project
from sbam_api.sow.models import SowVersion
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import (
    PipelineInput,
    PipelineStage,
    PipelineStatus,
    ResumeState,
    SowApproved,
)
from sbam_contracts.runs import pipeline_workflow_id
from sbam_workers import mirror
from sbam_workers.isatvon.carry_over import carry_over_workflow
from sbam_workers.workflows.create import create_workflow
from scope_change_db import DOC, TENANT, World
from sqlalchemy import select
from test_project_pipeline import FDE, environment, until
from workflows_db import real_models

REAL = {"create_workflow": create_workflow, "carry_over_workflow": carry_over_workflow}


def run_scope_change(w: World, doc: dict[str, Any]) -> PipelineStatus:
    """Approve v2 = `doc` and run the replacing pipeline to the Generate wait."""
    v2 = w.approve_v2(doc)
    with w.sessions() as s:
        project = s.get(Project, w.project)
        assert project is not None
        project.state = "SOW_REVIEW"  # where POST P/sow/new-version left it
        s.commit()
        version = s.get(SowVersion, v2)
        assert version is not None and version.sha256 is not None
        approval = SowApproved(sow_version_id=v2, sha256=version.sha256, actor_id=FDE)
    mirror.configure(w.sessions)

    async def scenario() -> PipelineStatus:
        async with environment(REAL) as env:
            inp = PipelineInput(
                tenant_id=TENANT,
                project_id=w.project,
                run_id=uuid.uuid4(),
                correlation_id="cid-e7-2",
                actor_id=FDE,
                search_attributes=False,
                resume=ResumeState(stage=PipelineStage.WAIT_SOW_APPROVAL, sow_approved=approval),
            )
            await start_pipeline(env.client, w.sessions, inp, with_search_attributes=False)
            handle = env.client.get_workflow_handle(
                pipeline_workflow_id(w.project), result_type=PipelineStatus
            )
            status = await until(handle, PipelineStage.WAIT_GENERATE)
            await handle.terminate("test done")
            return status

    with real_models():
        return asyncio.run(scenario())


def workflows(w: World) -> list[Workflow]:
    with w.sessions() as s:
        rows = list(s.scalars(select(Workflow).order_by(Workflow.major)))
        s.expunge_all()
        return rows


def test_e7_2_t04_the_new_major_version_rebuilds_only_the_citing_sub_skill() -> None:
    w = World()
    doc = copy.deepcopy(DOC)
    doc["features"][0]["name"] = "a1 with refunds"  # F-A-1: cited by A1 only
    status = run_scope_change(w, doc)
    assert status.stage is PipelineStage.WAIT_GENERATE
    old, new = workflows(w)
    assert (old.version, new.version) == ("1.0.0", "2.0.0")
    assert old.state == "FROZEN"  # the previous version is untouched
    assert new.sow_version_id == w.v2
    rows = w.rows(new.id)
    assert rows["MS01.1_A1"].state == "DRAFT"
    assert w.trail(rows["MS01.1_A1"].id) == ["INVALIDATED", "DRAFT"]
    for name in ("MS01.2_A2", "MS02.4_B2"):
        assert rows[name].state == "CERTIFIED" and rows[name].reused_from_id is not None
    assert {r.state for r in w.rows(old.id).values()} == {"CERTIFIED", "VALID"}
    with w.sessions() as s:
        project = s.get(Project, w.project)
        assert project is not None and project.state == "WORKFLOW_CREATED"
