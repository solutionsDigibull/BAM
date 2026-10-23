"""ISATVON breakdown guards (E8.2), shared by the extraction activity and the regenerate routes."""

from sbam_contracts.api import ErrorCode
from sbam_contracts.state_machines import SowVersionState
from sqlalchemy.orm import Session

from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.sow.models import SowVersion
from sbam_api.sow.repository import SowRepository, SowVersionRepository
from sbam_api.workflows.models import Workflow


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
