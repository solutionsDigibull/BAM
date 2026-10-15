"""Certification and freeze stages (owner: E14.x).

STUBS for the ProjectPipeline skeleton (E10.1): fixed activity names and Pydantic in/out
(sbam_contracts.pipeline). The owning issue replaces the bodies and keeps names and types.
"""

from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import FreezeResult, IdResult, IdsResult, WorkflowRef
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stage_stub_id, stub_id


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="certify_sub_skill")
async def certify_sub_skill(inp: BuildStageInput) -> IdResult:
    """Real: hashes the passing build, writes the certificate; non-retryable HashMismatch."""
    return IdResult(id=stage_stub_id(inp, "certify_sub_skill"))


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="certify_macros")
async def certify_macros(inp: WorkflowRef) -> IdsResult:
    return IdsResult(ids=[stub_id(inp, "certify_macros")])


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="validate_workflow")
async def validate_workflow(inp: WorkflowRef) -> IdResult:
    """Real: Workflow-level validation; a failure maps to workflow_validation_failed."""
    return IdResult(id=stub_id(inp, "validate_workflow"))


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="certify_workflow")
async def certify_workflow(inp: WorkflowRef) -> IdResult:
    return IdResult(id=stub_id(inp, "certify_workflow"))


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="freeze_and_version")
async def freeze_and_version(inp: WorkflowRef) -> FreezeResult:
    """Real: ordered freeze (Sub-Skills -> Macros -> Workflow) + MAJOR.0.PATCH (E14.4)."""
    return FreezeResult(workflow_id=inp.workflow_id, version="1.0.0")
