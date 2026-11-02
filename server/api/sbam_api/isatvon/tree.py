"""`GET W/macro-skills` (TD §8.8, E8.7): the Macro → Sub-Skill tree the ISATVON workbench shows.

Read-only and readable in every Workflow state: the grid decides from `workflow_state` and
`sow_approved` whether it is editable, and the edit endpoints enforce both again (409/423). Macro
Skills and their Sub-Skills come in `order`; a Workflow before extraction answers an empty tree.
Another tenant's or an unassigned project, or a Workflow of another project, is 404.
"""

import uuid

from sbam_contracts.isatvon_workbench import WorkbenchMacro, WorkbenchSubSkill, WorkbenchTree
from sbam_contracts.state_machines import SowVersionState, WorkflowState
from sqlalchemy.orm import Session

from sbam_api.core.conventions import not_found
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.sow.repository import SowVersionRepository


def workbench_row(sub: SubSkill) -> WorkbenchSubSkill:
    """One grid row; its `content_sha256` is the ETag an edit sends back as `If-Match`."""
    return WorkbenchSubSkill.model_validate(
        {
            "id": sub.id,
            "macro_id": sub.macro_skill_id,
            "state": sub.state,
            "content_sha256": sub.content_sha256,
            "validation_errors": sub.validation_errors,
            "sow_refs": sub.sow_refs,
            "sub_skill": sub.isatvon,
        }
    )


def workbench_tree(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> WorkbenchTree:
    from sbam_api.workflows.repository import WorkflowRepository  # the SOW package imports this

    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    sow_version = SowVersionRepository(session, ctx).get(workflow.sow_version_id)
    subs = SubSkillRepository(session, ctx)
    macros = [
        WorkbenchMacro(
            id=m.id,
            macro_category=m.macro_category,
            name=m.name,
            description=m.description,
            order=m.order,
            sub_skills=[workbench_row(s) for s in subs.of_macro(m.id)],
        )
        for m in MacroSkillRepository(session, ctx).of_workflow(workflow.id)
    ]
    return WorkbenchTree(
        workflow_id=workflow.id,
        workflow_state=WorkflowState(workflow.state),
        sow_approved=sow_version is not None
        and sow_version.state == SowVersionState.APPROVED.value,
        macros=macros,
    )
