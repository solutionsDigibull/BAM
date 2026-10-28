"""Tenant-scoped queries for Workflows (E8.1)."""

import uuid

from sqlalchemy import func, update

from sbam_api.state_machine.repository import StatefulRepository
from sbam_api.workflows.models import Workflow


class WorkflowRepository(StatefulRepository):
    """Use with a context that names the project."""

    model = Workflow

    def of_project(self) -> list[Workflow]:
        """Newest MAJOR first."""
        return list(self.session.scalars(self._scoped().order_by(Workflow.major.desc())))

    def by_sow_version(self, sow_version_id: uuid.UUID) -> Workflow | None:
        query = self._scoped(include_deleted=True).where(Workflow.sow_version_id == sow_version_id)
        found: Workflow | None = self.session.scalars(query).one_or_none()
        return found

    def next_major(self) -> int:
        query = self._scoped(include_deleted=True).with_only_columns(func.max(Workflow.major))
        return int(self.session.scalar(query) or 0) + 1

    def set_error_reason(self, workflow_id: uuid.UUID, reason: str | None) -> None:
        """Why the last extraction failed (a code and ids). An UPDATE, not the loaded row: Macro
        Skills fail in parallel and must not trip over the Workflow's row version."""
        query = update(Workflow).where(
            Workflow.id == workflow_id, Workflow.tenant_id == self.ctx.tenant_id
        )
        if self.ctx.project_id is not None:
            query = query.where(Workflow.project_id == self.ctx.project_id)
        self.session.execute(query.values(error_reason=None if reason is None else reason[:300]))
