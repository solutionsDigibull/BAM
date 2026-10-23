"""Workflow reads and the one edit a person may make: the name (E8.1).

SBAM creates Workflows (`sbam_workers.workflows.create`); there is no create here.
"""

import uuid

from sbam_contracts.projects import WorkflowRename, WorkflowSummary
from sbam_contracts.state_machines import MachineName, machine
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import check_if_match, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository

LOCKED = machine(MachineName.WORKFLOW).locked  # CERTIFIED, FROZEN


def summary(row: Workflow) -> WorkflowSummary:
    return WorkflowSummary.model_validate(
        {c: getattr(row, c) for c in WorkflowSummary.model_fields}
    )


def list_workflows(session: Session, user: CurrentUser, project_id: uuid.UUID) -> list[Workflow]:
    return WorkflowRepository(session, user.context(project_id)).of_project()


def rename(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    body: WorkflowRename,
    if_match: str | None,
) -> Workflow:
    """If-Match (428/412); a CERTIFIED or FROZEN Workflow is 423 whatever the ETag."""
    repo = WorkflowRepository(session, user.context(project_id))
    row: Workflow | None = repo.get_for_update(workflow_id)
    if row is None:
        raise not_found()
    check_if_match(if_match, row.row_version, frozen=row.state in LOCKED)
    if row.name == body.name:
        return row
    before = {"name": row.name}
    row.name = body.name
    repo.update(row, row.row_version)
    record(
        session,
        tenant_id=row.tenant_id,
        actor_id=str(user.user_id),
        action="WORKFLOW_RENAMED",
        entity_type="workflow",
        entity_id=str(row.id),
        before=before,
        after={"name": row.name},
        correlation_id=correlation_id_var.get() or "unknown",
    )
    session.commit()
    return row
