"""E8.2 on Temporal's time-skipping server: the ProjectPipeline with the real `create_workflow` and
the real pass-1 step (`extract_macros`, wired to a fake Bifrost) on the real tables (SQLite). The
earlier stages are the usual stand-ins; the SOW is approved by the test, then the signal sent."""

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from knowledge_db import TENANT, World
from llm_db import FakeBifrost, completion
from macros_db import Pass1, answer, macro
from sbam_api.audit.models import AuditLog
from sbam_api.pipeline.service import start_pipeline
from sbam_api.projects.models import Project
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import (
    IdsResult,
    PipelineInput,
    PipelineStage,
    PipelineStatus,
    SowApproved,
    WorkflowRef,
)
from sbam_contracts.runs import pipeline_workflow_id
from sbam_workers import mirror
from sbam_workers.isatvon.macros import extract_macros
from sbam_workers.llm.store import LlmScope
from sbam_workers.workflows.create import create_workflow
from sqlalchemy import select
from temporalio import activity
from test_project_pipeline import FDE, environment, until
from workflows_db import real_models

FOUR = [
    macro("Investor Registration", "M-REGISTER"),
    macro("Portfolio Tracking", "M-PORTFOLIO"),
    macro("Investor Reporting", "M-REPORTS"),
    macro("Investor Support", "M-SUPPORT"),
]


def pass1_activity(p: Pass1, bifrost: FakeBifrost) -> Callable[..., Any]:
    """`extract_macro_skills` as the worker runs it, with the test's Bifrost and database."""

    @activity.defn(name="extract_macro_skills")
    async def extract(inp: WorkflowRef) -> IdsResult:
        info = activity.info()
        scope = LlmScope(
            tenant_id=inp.tenant_id,
            project_id=inp.project_id,
            correlation_id=inp.correlation_id,
            temporal_workflow_id=info.workflow_id or "",
            activity_id=info.activity_id,
            attempt=info.attempt,
        )
        return IdsResult(ids=await extract_macros(p.deps(bifrost), inp, scope))

    return extract


def run(
    tmp_path: Path,
    bifrost: FakeBifrost,
    until_stage: PipelineStage,
) -> tuple[Pass1, PipelineStatus]:
    p = Pass1(World(tmp_path), with_workflow=False)
    with p.w.sessions() as s:
        project = s.get(Project, p.w.project)
        assert project is not None
        project.state = "IDLE"
        s.commit()
    mirror.configure(p.w.sessions)
    overrides = {
        "create_workflow": create_workflow,
        "extract_macro_skills": pass1_activity(p, bifrost),
    }

    async def scenario() -> PipelineStatus:
        async with environment(overrides) as env:
            inp = PipelineInput(
                tenant_id=TENANT,  # the knowledge world's tenant
                project_id=p.w.project,
                run_id=uuid.uuid4(),
                correlation_id="cid-e8-2",
                actor_id=FDE,
                search_attributes=False,
            )
            await start_pipeline(env.client, p.w.sessions, inp, with_search_attributes=False)
            handle = env.client.get_workflow_handle(
                pipeline_workflow_id(p.w.project), result_type=PipelineStatus
            )
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            approval = SowApproved(sow_version_id=p.version, sha256=p.sha, actor_id=FDE)
            await handle.signal("sow_approved", approval)
            status = await until(handle, until_stage)
            if until_stage is not PipelineStage.FAILED:
                await handle.terminate("test done")
            return status

    with real_models():
        return p, asyncio.run(scenario())


def workflow_of(p: Pass1) -> Workflow:
    with p.w.sessions() as s:
        row = s.scalars(select(Workflow).where(Workflow.project_id == p.w.project)).one()
        s.expunge(row)
        return row


def failures(p: Pass1) -> list[dict[str, Any]]:
    with p.w.sessions() as s:
        rows = s.scalars(
            select(AuditLog).where(
                AuditLog.action == "pipeline.failed", AuditLog.entity_id == str(p.w.project)
            )
        )
        return [dict(r.after or {}) for r in rows]


def test_e8_2_the_pipeline_extracts_the_macros_and_moves_on(tmp_path: Path) -> None:
    p, status = run(tmp_path, FakeBifrost([completion(answer(*FOUR))]), PipelineStage.WAIT_GENERATE)
    assert status.stage is PipelineStage.WAIT_GENERATE
    workflow = workflow_of(p)
    p.workflow = workflow.id
    assert [m.macro_category for m in p.macros()] == [
        "MS01_Investor_Registration",
        "MS02_Portfolio_Tracking",
        "MS03_Investor_Reporting",
        "MS04_Investor_Support",
    ]
    assert workflow.state == "ISATVON_REVIEW" and workflow.error_reason is None


def test_e8_2_t03_an_uncovered_module_ends_in_extraction_failed_with_the_reason(
    tmp_path: Path,
) -> None:
    without = [m for m in FOUR if m["sow_refs"] != ["M-REPORTS"]]
    p, status = run(tmp_path, FakeBifrost([completion(answer(*without))]), PipelineStage.FAILED)
    assert status.failed_stage is PipelineStage.EXTRACT_MACROS
    assert status.last_error == "extract_macro_skills: ModuleNotCovered"
    workflow = workflow_of(p)
    assert workflow.state == "EXTRACTION_FAILED"
    assert workflow.error_reason == "module M-REPORTS not covered"
    assert failures(p) == [
        {"stage": "extract_macros", "error": "ModuleNotCovered", "next_action": "retry"}
    ]
    p.workflow = workflow.id
    assert p.macros() == []


def test_e8_2_t05_openrouter_down_ends_in_extraction_failed_llm_unavailable(
    tmp_path: Path,
) -> None:
    down = FakeBifrost([httpx.Response(503, json={"error": "upstream down"})])
    p, status = run(tmp_path, down, PipelineStage.FAILED)
    assert status.last_error == "extract_macro_skills: LLM_UNAVAILABLE"
    workflow = workflow_of(p)
    assert workflow.state == "EXTRACTION_FAILED"
    assert workflow.error_reason == "LLM_UNAVAILABLE"
    assert [f["error"] for f in failures(p)] == ["LLM_UNAVAILABLE"]


def test_e8_2_t07_a_retried_activity_keeps_one_macro_set(tmp_path: Path) -> None:
    """The first attempt stores the macros and then its worker 'dies' (the result is lost); the
    retry returns the stored set without a second model call."""
    bifrost = FakeBifrost([completion(answer(*FOUR))])
    p = Pass1(World(tmp_path))
    first = p.run(bifrost, attempt=1)
    retried = p.run(bifrost, attempt=2)
    assert retried == first and len(bifrost.requests) == 1
    assert len(p.macros()) == 4
    assert uuid.UUID(str(first[0]))  # ids are real UUIDs, not stub ids
