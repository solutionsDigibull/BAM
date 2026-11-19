"""Tenant-scoped queries for `workflow_versions`. Rows are only inserted."""

import uuid
from typing import Any

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.versioning.models import WorkflowVersionRow


class WorkflowVersionRepository(TenantScopedRepository[WorkflowVersionRow]):
    """Use with a context that names the project."""

    model = WorkflowVersionRow

    def of_workflow(self, workflow_id: uuid.UUID) -> list[WorkflowVersionRow]:
        """Newest first."""
        query = self._scoped().where(WorkflowVersionRow.workflow_id == workflow_id)
        rows = self.session.scalars(query)
        return sorted(rows, key=lambda r: (r.major, r.patch), reverse=True)

    def of_project(self) -> list[WorkflowVersionRow]:
        """Every frozen version of the project, newest first."""
        rows = self.session.scalars(self._scoped())
        return sorted(rows, key=lambda r: (r.major, r.patch), reverse=True)

    def record(self, values: dict[str, Any]) -> WorkflowVersionRow:
        """Insert once per (workflow_id, version); a retried freeze gets the row it wrote."""
        found = self.session.scalars(
            self._scoped().where(
                WorkflowVersionRow.workflow_id == values["workflow_id"],
                WorkflowVersionRow.version == values["version"],
            )
        ).first()
        if found is not None:
            return found
        return self.add(WorkflowVersionRow(**values))
