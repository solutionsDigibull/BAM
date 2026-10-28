"""E8.3 on Temporal's time-skipping server: the ProjectPipeline with the real `create_workflow`,
pass 1 and pass 2 (wired to one fake Bifrost that answers by prompt and Macro Skill) on the real
tables (SQLite). Pass 2 runs per Macro Skill in parallel, so the answers can't be a sequence."""

import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from knowledge_db import TENANT, World
from llm_db import FakeBifrost, completion
from macros_db import Pass1, macro
from macros_db import answer as macros_answer
from pipeline_db import new_project
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.pipeline.service import start_pipeline
from sbam_api.projects.models import Project
from sbam_contracts.pipeline import (
    IdsResult,
    MacroRef,
    PipelineInput,
    PipelineStage,
    PipelineStatus,
    SowApproved,
    WorkflowRef,
)
from sbam_contracts.runs import pipeline_workflow_id
from sbam_workers import mirror
from sbam_workers.isatvon.sub_skills import extract_for_macro
from sbam_workers.llm.store import LlmScope
from sbam_workers.pipeline.project_pipeline import MAX_PARALLEL_EXTRACTIONS
from sbam_workers.workflows.create import create_workflow
from sqlalchemy import select
from sub_skills_db import answer, proposal
from temporalio import activity
from test_macro_extraction import failures, pass1_activity, workflow_of
from test_project_pipeline import FDE, approve, environment, start, until
from test_project_pipeline import TENANT as PIPELINE_TENANT
from test_project_pipeline import run as pipeline_run
from workflows_db import real_models

TWO_MACROS = macros_answer(
    macro("Investor Onboarding", "M-REGISTER", "M-PORTFOLIO"),
    macro("Investor Service", "M-REPORTS", "M-SUPPORT"),
)
MS01 = answer(
    *(
        proposal(f"Investor {name} Step {n}", f"F-{name.upper()}-{n}")
        for name in ("Register", "Portfolio")
        for n in (1, 2)
    )
)
MS02 = answer(
    *(
        proposal(f"Investor {name} Step {n}", f"F-{name.upper()}-{n}")
        for name in ("Reports", "Support")
        for n in (1, 2)
    )
)


class Routed(FakeBifrost):
    """Answers by prompt, and in pass 2 by the Macro Skill the request is about."""

    def __init__(self, sub_skills: dict[str, str]) -> None:
        super().__init__([])
        self.sub_skills = sub_skills

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        meta = body.get("metadata", {})
        if meta.get("prompt_id") == "isatvon-macros":
            return httpx.Response(200, json=completion(TWO_MACROS))
        return httpx.Response(200, json=completion(self.sub_skills[meta["macro_category"]]))


def pass2_activity(p: Pass1, bifrost: FakeBifrost, in_order: bool = False) -> Callable[..., Any]:
    """`extract_sub_skills` as the worker runs it, with the test's Bifrost and database.
    `in_order`: a Macro Skill waits until the one before it has stored its Sub-Skills, so a test
    can tell "a later failure keeps the earlier rows" from "the earlier one never ran"."""

    @activity.defn(name="extract_sub_skills")
    async def extract(inp: MacroRef) -> IdsResult:
        if in_order:
            await _after_previous(p, inp.macro_skill_id)
        info = activity.info()
        scope = LlmScope(
            tenant_id=inp.tenant_id,
            project_id=inp.project_id,
            correlation_id=inp.correlation_id,
            temporal_workflow_id=info.workflow_id or "",
            activity_id=info.activity_id,
            attempt=info.attempt,
        )
        ids = await extract_for_macro(p.deps(bifrost), inp, scope)
        DONE.add(inp.macro_skill_id)
        return IdsResult(ids=ids)

    return extract


DONE: set[uuid.UUID] = set()  # Macro Skills whose pass 2 has returned (after its commit)


async def _after_previous(p: Pass1, macro_skill_id: uuid.UUID) -> None:
    """Wait for the previous Macro Skill's activity to return, not for its rows to show: the test
    world's SQLite is one shared connection, so rows show before they are committed."""

    def previous_id() -> uuid.UUID | None:
        with p.w.sessions() as s:
            macro = s.get(MacroSkill, macro_skill_id)
            if macro is None or macro.order == 1:
                return None
            return s.scalars(
                select(MacroSkill.id).where(
                    MacroSkill.workflow_id == macro.workflow_id,
                    MacroSkill.order == macro.order - 1,
                )
            ).one()

    previous = await asyncio.to_thread(previous_id)
    for _ in range(200):
        if previous is None or previous in DONE:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the previous Macro Skill's pass 2 never returned")


def run(
    tmp_path: Path, bifrost: FakeBifrost, until_stage: PipelineStage, in_order: bool = False
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
        "extract_sub_skills": pass2_activity(p, bifrost, in_order),
    }

    async def scenario() -> PipelineStatus:
        async with environment(overrides) as env:
            inp = PipelineInput(
                tenant_id=TENANT,
                project_id=p.w.project,
                run_id=uuid.uuid4(),
                correlation_id="cid-e8-3",
                actor_id=FDE,
                search_attributes=False,
            )
            await start_pipeline(env.client, p.w.sessions, inp, with_search_attributes=False)
            handle = env.client.get_workflow_handle(
                pipeline_workflow_id(p.w.project), result_type=PipelineStatus
            )
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await handle.signal(
                "sow_approved", SowApproved(sow_version_id=p.version, sha256=p.sha, actor_id=FDE)
            )
            status = await until(handle, until_stage)
            if until_stage is not PipelineStage.FAILED:
                await handle.terminate("test done")
            return status

    with real_models():
        return p, asyncio.run(scenario())


def sub_skills_of(p: Pass1) -> dict[str, list[SubSkill]]:
    p.workflow = workflow_of(p).id
    by_macro: dict[str, list[SubSkill]] = {m.macro_category: [] for m in p.macros()}
    names = {m.id: m.macro_category for m in p.macros()}
    with p.w.sessions() as s:
        rows = s.scalars(select(SubSkill).order_by(SubSkill.macro_skill_id, SubSkill.order))
        for row in rows:
            by_macro[names[row.macro_skill_id]].append(row)
        s.expunge_all()
    return by_macro


def test_e8_3_the_pipeline_breaks_every_macro_down_and_moves_to_review(tmp_path: Path) -> None:
    bifrost = Routed({"MS01_Investor_Onboarding": MS01, "MS02_Investor_Service": MS02})
    p, status = run(tmp_path, bifrost, PipelineStage.WAIT_GENERATE)
    assert status.stage is PipelineStage.WAIT_GENERATE
    found = sub_skills_of(p)
    assert [r.skill_id for r in found["MS01_Investor_Onboarding"]] == [
        "MS01.1_Investor_Register_Step_1",
        "MS01.2_Investor_Register_Step_2",
        "MS01.3_Investor_Portfolio_Step_1",
        "MS01.4_Investor_Portfolio_Step_2",
    ]
    assert len(found["MS02_Investor_Service"]) == 4
    last = found["MS01_Investor_Onboarding"][-1].isatvon["isatvon_definition"]["n_notification"]
    assert last["next"] == "MS02_Investor_Service"
    assert {r.state for rows in found.values() for r in rows} == {"VALID"}
    workflow = workflow_of(p)
    assert workflow.state == "ISATVON_REVIEW" and workflow.error_reason is None


def test_e8_3_t07_a_macro_without_sub_skills_fails_extraction_and_the_other_keeps_its_rows(
    tmp_path: Path,
) -> None:
    bifrost = Routed({"MS01_Investor_Onboarding": MS01, "MS02_Investor_Service": "{not json"})
    p, status = run(tmp_path, bifrost, PipelineStage.FAILED, in_order=True)
    assert status.failed_stage is PipelineStage.EXTRACT_SUB_SKILLS
    assert status.last_error == "extract_sub_skills: SchemaInvalidAfterRetries"
    workflow = workflow_of(p)
    assert workflow.state == "EXTRACTION_FAILED"
    assert workflow.error_reason == "MS02_Investor_Service: SCHEMA_INVALID after 4"
    assert [f["error"] for f in failures(p)] == ["SchemaInvalidAfterRetries"]
    found = sub_skills_of(p)
    assert found["MS02_Investor_Service"] == []
    assert len(found["MS01_Investor_Onboarding"]) == 4  # retained


def test_e8_3_pass_2_runs_at_most_four_macros_at_once() -> None:
    """Six Macro Skills (stand-ins): never more than MAX_PARALLEL_EXTRACTIONS extracting at once."""
    running = {"now": 0, "most": 0}

    @activity.defn(name="extract_macro_skills")
    async def six_macros(inp: WorkflowRef) -> IdsResult:
        return IdsResult(ids=[uuid.uuid5(inp.workflow_id, f"macro:{n}") for n in range(6)])

    @activity.defn(name="extract_sub_skills")
    async def slow(inp: MacroRef) -> IdsResult:
        running["now"] += 1
        running["most"] = max(running["most"], running["now"])
        await asyncio.sleep(0.2)
        running["now"] -= 1
        return IdsResult(ids=[uuid.uuid5(inp.macro_skill_id, "sub:1")])

    async def scenario(sessions: Any) -> None:
        project = new_project(sessions, PIPELINE_TENANT)
        overrides = {"extract_macro_skills": six_macros, "extract_sub_skills": slow}
        async with environment(overrides) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await handle.terminate("test done")

    pipeline_run(scenario)
    assert running["most"] == MAX_PARALLEL_EXTRACTIONS
