"""Build stages (owners: E11.x, #107).

STUBS for the ProjectPipeline skeleton (E10.1): fixed activity names and Pydantic in/out
(sbam_contracts.pipeline). The owning issue replaces the bodies and keeps names and types.
"""

from sbam_contracts.pipeline import IdResult, SubSkillBuildInput, SubSkillBuildResult, WorkflowRef
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stub_id


@activity_for(TaskQueue.BUILD)
@activity.defn(name="build_schema_sql")
async def build_schema_sql(inp: WorkflowRef) -> IdResult:
    """Real: Workflow-level schema.sql (TD §12.6); non-retryable SchemaInvalid."""
    return IdResult(id=stub_id(inp, "schema_sql"))


@activity_for(TaskQueue.BUILD)
@activity.defn(name="build_sub_skill")
async def build_sub_skill(inp: SubSkillBuildInput) -> SubSkillBuildResult:
    """#107 replaces the SubSkillBuild internals (render, generate, gates, tests, correction)."""
    return SubSkillBuildResult(sub_skill_id=inp.sub_skill_id, certified=True)
