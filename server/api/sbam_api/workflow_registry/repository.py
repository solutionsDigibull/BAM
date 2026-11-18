"""Tenant-scoped queries for registered Workflows (#760). Rows are inserted, and superseded once."""

import uuid

from sbam_contracts.workflow_registry import RegistryKind, RegistryStatus
from sqlalchemy import Select

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.workflow_registry.models import RegisteredWorkflowRow


def version_key(version: str) -> tuple[int, int]:
    """MAJOR.0.PATCH as a sortable pair (C-32)."""
    major, _, patch = version.split(".")
    return int(major), int(patch)


def _kind_rank(row: RegisteredWorkflowRow) -> int:
    return 0 if row.kind == RegistryKind.STAGING.value else 1


class RegisteredWorkflowRepository(TenantScopedRepository[RegisteredWorkflowRow]):
    """Use with a context that names the project."""

    model = RegisteredWorkflowRow

    @staticmethod
    def _of_kind(
        query: Select[tuple[RegisteredWorkflowRow]], kind: RegistryKind | None
    ) -> Select[tuple[RegisteredWorkflowRow]]:
        return query if kind is None else query.where(RegisteredWorkflowRow.kind == kind.value)

    def of_version(
        self, workflow_id: uuid.UUID, version: str, kind: RegistryKind | None = None
    ) -> RegisteredWorkflowRow | None:
        """One version's entry of `kind`. With no kind, the staging entry first, then production."""
        query = self._scoped().where(
            RegisteredWorkflowRow.workflow_id == workflow_id,
            RegisteredWorkflowRow.version == version,
        )
        rows = list(self.session.scalars(self._of_kind(query, kind)))
        return min(rows, key=_kind_rank, default=None)

    def of_workflow(
        self, workflow_id: uuid.UUID, kind: RegistryKind | None = None
    ) -> list[RegisteredWorkflowRow]:
        """Every version of one Workflow (of `kind`, or both), newest first."""
        query = self._scoped().where(RegisteredWorkflowRow.workflow_id == workflow_id)
        rows = list(self.session.scalars(self._of_kind(query, kind)))
        return sorted(rows, key=lambda row: version_key(row.version), reverse=True)

    def current(
        self, workflow_id: uuid.UUID, kind: RegistryKind | None = None
    ) -> RegisteredWorkflowRow | None:
        """The newest version that is still enabled; with no kind, staging before production."""
        enabled = [
            row
            for row in self.of_workflow(workflow_id, kind)
            if row.status == RegistryStatus.REGISTERED.value
        ]
        return min(enabled, key=_kind_rank, default=None)  # min is stable: newest stays first

    def all_rows(self) -> list[RegisteredWorkflowRow]:
        """Every entry of the context's scope, for the reconcile."""
        return list(self.session.scalars(self._scoped()))
