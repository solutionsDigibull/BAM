"""Certification and freeze stages (owner: E14.x).

STUBS for the ProjectPipeline skeleton (E10.1): fixed activity names and Pydantic in/out
(sbam_contracts.pipeline). The owning issue replaces the bodies and keeps names and types.
"""

from sbam_contracts.build import BuildStageInput
from sbam_contracts.pipeline import FreezeResult, IdResult, IdsResult, WorkflowRef
from sbam_contracts.runs import TaskQueue
from temporalio import activity

from sbam_workers.registry import activity_for
from sbam_workers.stub_ids import stage_stub_id, stub_activity, stub_id


@activity.defn(name="certify_sub_skill")  # registered by `certify/subskill.py`; a typed handle
async def certify_sub_skill(inp: BuildStageInput) -> IdResult:
    """Real (E14.1): checks the evidence, writes the certificate, approves the manifest."""
    return IdResult(id=stage_stub_id(inp, "certify_sub_skill"))


@activity.defn(name="reuse_certifications")  # registered by `certify/reuse_step.py`; a typed handle
async def reuse_certifications(inp: WorkflowRef) -> IdsResult:
    """Real (E14.3): an unchanged carried-over Sub-Skill's certificate is reused or refused."""
    return IdsResult(ids=[])


@activity.defn(name="certify_macros")  # registered by `certify/workflow.py`; a typed handle
async def certify_macros(inp: WorkflowRef) -> IdsResult:
    """Real (E14.2): a MACRO_SKILL certificate per Macro whose Sub-Skills are all certified."""
    return IdsResult(ids=[stub_id(inp, "certify_macros")])


@activity.defn(name="validate_workflow")  # registered by `certify/workflow.py`; a typed handle
async def validate_workflow(inp: WorkflowRef) -> IdResult:
    """Real (E14.2): Workflow-level validation; a failure maps to workflow_validation_failed."""
    return IdResult(id=stub_id(inp, "validate_workflow"))


@activity.defn(name="certify_workflow")  # registered by `certify/workflow.py`; a typed handle
async def certify_workflow(inp: WorkflowRef) -> IdResult:
    """Real (E14.2): the WORKFLOW certificate on the newest manifest hash."""
    return IdResult(id=stub_id(inp, "certify_workflow"))


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="freeze_and_version")
@stub_activity
async def freeze_and_version(inp: WorkflowRef) -> FreezeResult:
    """Real: ordered freeze (Sub-Skills -> Macros -> Workflow) + MAJOR.0.PATCH (E14.4)."""
    return FreezeResult(workflow_id=inp.workflow_id, version="1.0.0")
