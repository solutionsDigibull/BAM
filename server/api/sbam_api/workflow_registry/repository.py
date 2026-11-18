"""Tenant-scoped queries for registered Workflows (#760). Rows are inserted, and superseded once."""

import uuid

from sbam_contracts.workflow_registry import RegistryStatus

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.workflow_registry.models import RegisteredWorkflowRow


def version_key(version: str) -> tuple[int, int]:
    """MAJOR.0.PATCH as a sortable pair (C-32)."""
    major, _, patch = version.split(".")
    return int(major), int(patch)


class RegisteredWorkflowRepository(TenantScopedRepository[RegisteredWorkflowRow]):
    """Use with a context that names the project."""

    model = RegisteredWorkflowRow

    def of_version(self, workflow_id: uuid.UUID, version: str) -> RegisteredWorkflowRow | None:
        query = self._scoped().where(
            RegisteredWorkflowRow.workflow_id == workflow_id,
            RegisteredWorkflowRow.version == version,
        )
        return self.session.scalars(query).first()

    def of_workflow(self, workflow_id: uuid.UUID) -> list[RegisteredWorkflowRow]:
        """Every version of one Workflow, newest first."""
        query = self._scoped().where(RegisteredWorkflowRow.workflow_id == workflow_id)
        rows = list(self.session.scalars(query))
        return sorted(rows, key=lambda row: version_key(row.version), reverse=True)

    def current(self, workflow_id: uuid.UUID) -> RegisteredWorkflowRow | None:
        """The newest version that is still enabled."""
        for row in self.of_workflow(workflow_id):
            if row.status == RegistryStatus.REGISTERED.value:
                return row
        return None

    def all_rows(self) -> list[RegisteredWorkflowRow]:
        """Every entry of the context's scope, for the reconcile."""
        return list(self.session.scalars(self._scoped()))
