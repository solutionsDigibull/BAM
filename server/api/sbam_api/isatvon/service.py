"""ISATVON breakdown guards (E8.2), shared by the extraction activity and the regenerate routes, and
the workbench's revalidation (E8.3)."""

import uuid

from sbam_contracts.api import ErrorCode
from sbam_contracts.isatvon14 import DEFAULT_LIBRARY_REGISTRY, validate_sub_skill
from sbam_contracts.isatvon_workbench import SubSkillValidation, ValidationReport
from sbam_contracts.state_machines import SowVersionState, SubSkillState
from sqlalchemy.orm import Session

from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.sow.models import SowVersion
from sbam_api.sow.repository import SowRepository, SowVersionRepository
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository


def sow_not_approved() -> ApiError:
    return ApiError(
        409,
        ErrorCode.SOW_NOT_APPROVED,
        "SOW not approved",
        "The Workflow's SOW version is not the approved one; nothing was extracted.",
    )


def require_approved_sow(session: Session, ctx: TenantContext, workflow: Workflow) -> SowVersion:
    """The Workflow's SOW version, if it is APPROVED and still the SOW's approved version; else 409
    SOW_NOT_APPROVED (e.g. a newer version was approved since: extract that Workflow instead)."""
    version: SowVersion | None = SowVersionRepository(session, ctx).get(workflow.sow_version_id)
    if version is None or version.state != SowVersionState.APPROVED.value:
        raise sow_not_approved()
    sow = SowRepository(session, ctx).of_project()
    if sow is None or sow.approved_version_id != version.id or version.structured is None:
        raise sow_not_approved()
    return version


# Before the build these follow the validator; later states (BUILDING, CERTIFIED, ...) are reported
# as they are, with the findings beside them.
_CHECKED = frozenset({SubSkillState.DRAFT, SubSkillState.VALID, SubSkillState.INVALID})


def revalidate(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> ValidationReport:
    """`POST W/isatvon/validate`: every Sub-Skill of the Workflow through the same TD §10.4
    validator as extraction and the build's stage A. Read-only: nothing is stored or moved."""
    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    categories = {
        m.id: m.macro_category for m in MacroSkillRepository(session, ctx).of_workflow(workflow.id)
    }
    report: list[SubSkillValidation] = []
    for row in SubSkillRepository(session, ctx).of_workflow(workflow.id):
        findings = validate_sub_skill(
            row.isatvon,
            macro_category=categories.get(row.macro_skill_id),
            library_registry=DEFAULT_LIBRARY_REGISTRY,
        )
        state = SubSkillState(row.state)
        if state in _CHECKED:
            state = SubSkillState.INVALID if findings else SubSkillState.VALID
        report.append(SubSkillValidation(id=row.id, state=state, validation_errors=findings))
    return ValidationReport(sub_skills=report)
