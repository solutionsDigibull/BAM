"""Workflow creation and ISATVON extraction (owners: #91, E8.x).

STUBS for the ProjectPipeline skeleton (E10.1): fixed activity names and Pydantic in/out
(sbam_contracts.pipeline). The owning issue replaces the bodies and keeps names and types.
"""

from sbam_contracts.pipeline import (
    CreateWorkflowInput,
    CreateWorkflowResult,
    IdsResult,
    MacroRef,
    WorkflowRef,
)
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stub_id


@activity_for(TaskQueue.LLM)
@activity.defn(name="create_workflow")
async def create_workflow(inp: CreateWorkflowInput) -> CreateWorkflowResult:
    """#91: re-check the SOW is APPROVED with this sha256 (HashMismatch, non-retryable), upsert the
    Workflow on (project_id, sow_version_id). The pipeline applies the state transitions."""
    return CreateWorkflowResult(workflow_id=stub_id(inp, f"workflow:{inp.sow_version_id}"))


@activity_for(TaskQueue.LLM)
@activity.defn(name="extract_macro_skills")
async def extract_macro_skills(inp: WorkflowRef) -> IdsResult:
    """Real: Macro Skills from the approved SOW (pass 1)."""
    return IdsResult(ids=[stub_id(inp, f"macro:{n}") for n in (1, 2)])


@activity_for(TaskQueue.LLM)
@activity.defn(name="extract_sub_skills")
async def extract_sub_skills(inp: MacroRef) -> IdsResult:
    """Real: Sub-Skills (ISATVON 1.4) of one Macro Skill (pass 2); runs in parallel per macro."""
    return IdsResult(ids=[stub_id(inp, f"sub:{inp.macro_skill_id}:{n}") for n in (1, 2)])
