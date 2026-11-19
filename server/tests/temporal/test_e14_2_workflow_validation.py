"""E14.2 in the pipeline: a failing `validate_workflow` takes the Workflow from CERTIFYING to
WORKFLOW_VALIDATION_FAILED and stops the run.

The activity is the real `WorkflowCertification.run_validate_workflow` (verdict, audit row,
non-retryable `WorkflowValidationFailed`) on the pipeline's SQLite. Only its loading is replaced:
the pipeline's Workflow, Macro and Sub-Skill ids are stand-ins without rows (pipeline_db), so the
facts are given (a DAG that loops) and the run check is skipped.
"""

import uuid
from collections.abc import Coroutine
from typing import Any

from pipeline_db import actions, new_project, workflow_rows
from sbam_api.db.repository import TenantContext
from sbam_contracts.manifest import Dag, WorkflowManifest
from sbam_contracts.pipeline import IdResult, PipelineStage, PipelineStatus, WorkflowRef
from sbam_workers.certify.workflow import (
    VALIDATION_ACTION,
    Child,
    Facts,
    Tree,
    WorkflowCertification,
)
from sbam_workers.certify.workflow_checks import ChildSkill
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from test_project_pipeline import (
    TENANT,
    approve,
    environment,
    finish,
    generate,
    run,
    start,
    until,
)

BUNDLE = "b" * 64


def looping_facts(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> Facts:
    nodes: list[dict[str, Any]] = [
        {"id": "MS01_Access", "kind": "macro"},
        {"id": "MS01.1_Login", "kind": "skill"},
        {"id": "MS01.2_Verify", "kind": "skill"},
    ]
    edges: list[dict[str, Any]] = [
        {"from": "MS01_Access", "to": "MS01.1_Login", "kind": "contains"},
        {"from": "MS01.1_Login", "to": "MS01.2_Verify", "kind": "data", "field": "otp"},
        {"from": "MS01.2_Verify", "to": "MS01.1_Login", "kind": "data", "field": "token"},
    ]
    children = tuple(
        Child(
            uuid.uuid4(),
            uuid.uuid4(),
            ChildSkill(skill_id, "MS01_Access", "CERTIFIED", f"sha256:{BUNDLE}", BUNDLE),
            uuid.uuid4(),
        )
        for skill_id in ("MS01.1_Login", "MS01.2_Verify")
    )
    manifest = WorkflowManifest.model_construct(dag=Dag(nodes=nodes, edges=edges))
    return Facts(Tree((), children), uuid.uuid4(), "sha256:" + "c" * 64, manifest, "", {})


class Given(WorkflowCertification):
    @staticmethod
    def _workflow(session: Session, ctx: TenantContext, inp: WorkflowRef) -> None:
        return None  # the pipeline's Workflow is a stand-in row of another table


def test_e14_2_t03_a_failing_validation_moves_the_workflow_to_validation_failed() -> None:
    holder: dict[str, Any] = {}

    async def scenario(sessions: sessionmaker[Session]) -> tuple[uuid.UUID, PipelineStatus]:
        stages = Given(sessions, looping_facts)

        @activity.defn(name="validate_workflow")
        async def validate(inp: WorkflowRef) -> IdResult:
            return await stages.run_validate_workflow(inp)

        project = new_project(sessions, TENANT)
        async with environment({"validate_workflow": validate}) as env:
            handle = await start(env, sessions, project)
            await until(handle, PipelineStage.WAIT_SOW_APPROVAL)
            await approve(handle)
            await until(handle, PipelineStage.WAIT_GENERATE)
            await generate(handle)
            return project, await finish(handle)

    def wrapped(sessions: sessionmaker[Session]) -> Coroutine[Any, Any, Any]:
        holder["s"] = sessions
        return scenario(sessions)

    project, status = run(wrapped)
    sessions = holder["s"]
    assert status.stage is PipelineStage.FAILED
    assert status.failed_stage is PipelineStage.VALIDATE_WORKFLOW
    assert status.last_error == "validate_workflow: WorkflowValidationFailed"
    [wf] = workflow_rows(sessions, project)
    assert wf.state == "WORKFLOW_VALIDATION_FAILED"
    flow = actions(sessions, "workflow", wf.id)
    assert flow[flow.index("all_sub_skills_certified") :] == [
        "all_sub_skills_certified",
        VALIDATION_ACTION,
        "workflow_validation_failed",
    ]
    assert "workflow_certified" not in flow
