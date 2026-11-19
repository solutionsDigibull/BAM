"""E14.2-T08 in the pipeline: one Sub-Skill fails its build, the FDE fixes it and retries
(`rebuild_subskills` naming only it). Only that Sub-Skill is built and certified again; the Macro
and Workflow stages then run once, after it, and the Workflow is certified and frozen.

Real `ProjectPipeline` and `SubSkillBuild` on Temporal's time-skipping server, stand-in
activities (test_project_pipeline). The E14.2 stages are spies that answer like the stubs: the
real ones need the Workflow's rows, which the pipeline's stand-in ids do not have (their rules
are tested in tests/unit/test_e14_2_*.py).
"""

import uuid
from collections import Counter
from collections.abc import Coroutine
from typing import Any

from pipeline_db import actions, new_project, workflow_rows
from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import (
    IdResult,
    IdsResult,
    PipelineStage,
    PipelineStatus,
    RebuildSubSkills,
    WorkflowRef,
)
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError
from test_project_pipeline import (
    FDE,
    TENANT,
    approve,
    environment,
    finish,
    generate,
    run,
    start,
    until,
)

WORKFLOW_STAGES = ("certify_macros", "validate_workflow", "certify_workflow")


def test_e14_2_t08_retry_after_fix_recertifies_only_the_fixed_sub_skill() -> None:
    events: list[tuple[str, uuid.UUID | None]] = []
    broken: list[uuid.UUID] = []  # the Sub-Skill whose first build fails, until the FDE fixes it

    @activity.defn(name="render_skeleton")  # a TemplateError ends a SubSkillBuild uncertified
    async def build(inp: BuildStageInput) -> IdResult:
        events.append(("build", inp.sub_skill_id))
        if not broken:
            broken.append(inp.sub_skill_id)
        if inp.sub_skill_id == broken[0] and Counter(events)[("build", broken[0])] == 1:
            raise ApplicationError("template broke", type="TemplateError", non_retryable=True)
        return IdResult(id=uuid.uuid4())

    @activity.defn(name="certify_sub_skill")
    async def certify_sub_skill(inp: BuildStageInput) -> IdResult:
        events.append(("certify_sub_skill", inp.sub_skill_id))
        return IdResult(id=uuid.uuid4())

    @activity.defn(name="certify_macros")
    async def certify_macros(inp: WorkflowRef) -> IdsResult:
        events.append(("certify_macros", None))
        return IdsResult(ids=[uuid.uuid4()])

    @activity.defn(name="validate_workflow")
    async def validate_workflow(inp: WorkflowRef) -> IdResult:
        events.append(("validate_workflow", None))
        return IdResult(id=uuid.uuid4())

    @activity.defn(name="certify_workflow")
    async def certify_workflow(inp: WorkflowRef) -> IdResult:
        events.append(("certify_workflow", None))
        return IdResult(id=uuid.uuid4())

    spies = {
        "render_skeleton": build,
        "certify_sub_skill": certify_sub_skill,
        "certify_macros": certify_macros,
        "validate_workflow": validate_workflow,
        "certify_workflow": certify_workflow,
    }
    holder: dict[str, Any] = {}

    async def scenario(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, PipelineStatus, int]:
        project = new_project(sessions, TENANT)
        async with environment(spies) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            await until(handle, PipelineStage.WAIT_REBUILD)
            before_fix = len(events)
            assert not any(name in WORKFLOW_STAGES for name, _ in events)  # blocked until fixed
            rebuild = RebuildSubSkills(sub_skill_ids=broken, idempotency_key="rb-1", actor_id=FDE)
            await handle.signal("rebuild_subskills", rebuild)
            return project, await finish(handle), before_fix

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        holder["s"] = sessions
        return scenario(sessions)

    project, status, before_fix = run(wrapped)
    [fixed] = broken
    counts = Counter(events)
    others = {s for name, s in events if name == "build"} - {fixed}

    # the fixed Sub-Skill was built twice and certified once, after the retry
    assert counts[("build", fixed)] == 2
    assert counts[("certify_sub_skill", fixed)] == 1
    assert events.index(("certify_sub_skill", fixed)) >= before_fix
    # the others were built and certified once, before the fix, and never again
    assert len(others) == status.sub_skills_total - 1 == 3
    assert all(counts[("build", s)] == 1 == counts[("certify_sub_skill", s)] for s in others)
    after_fix = events[before_fix:]
    assert {s for name, s in after_fix if name in ("build", "certify_sub_skill")} == {fixed}
    # Macro + Workflow stages: once each, in order, after the fixed Sub-Skill's certificate
    assert [name for name, _ in after_fix if name in WORKFLOW_STAGES] == list(WORKFLOW_STAGES)
    assert events.index(("certify_macros", None)) > events.index(("certify_sub_skill", fixed))

    assert status.stage is PipelineStage.DONE
    assert status.sub_skills_done == status.sub_skills_total
    [wf] = workflow_rows(holder["s"], project)
    assert wf.state == "FROZEN"
    flow = actions(holder["s"], "workflow", wf.id)
    assert flow[flow.index("generate") :] == [
        "generate",
        "sub_skill_needs_review",
        "retry",
        "all_sub_skills_certified",
        "workflow_certified",
        "freeze",
    ]
