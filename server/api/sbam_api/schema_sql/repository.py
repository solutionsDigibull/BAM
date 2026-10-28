"""Tenant- and project-scoped `workflow_schemas` access (E9.4)."""

import uuid
from typing import Any

from sqlalchemy import func, select

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.schema_sql.models import WorkflowSchema


class WorkflowSchemaRepository(TenantScopedRepository[WorkflowSchema]):
    model = WorkflowSchema

    def latest(
        self, workflow_id: uuid.UUID, *, version: int | None = None, source: str | None = None
    ) -> WorkflowSchema | None:
        """The newest version (or `version`), optionally only of one source."""
        query = self._scoped().where(WorkflowSchema.workflow_id == workflow_id)
        if version is not None:
            query = query.where(WorkflowSchema.version == version)
        if source is not None:
            query = query.where(WorkflowSchema.source == source)
        return self.session.scalars(
            query.order_by(WorkflowSchema.version.desc()).limit(1)
        ).one_or_none()

    def add_version(self, values: dict[str, Any]) -> WorkflowSchema:
        """A new version after the newest one."""
        newest = self.session.scalar(
            select(func.max(WorkflowSchema.version)).where(
                WorkflowSchema.tenant_id == self.ctx.tenant_id,
                WorkflowSchema.workflow_id == values["workflow_id"],
            )
        )
        return self.add(WorkflowSchema(**values, version=(newest or 0) + 1))

    def record(self, values: dict[str, Any]) -> WorkflowSchema:
        """A build's result once per (workflow, sha256, source): a retried activity gets the row
        it already wrote instead of a second version."""
        query = self._scoped().where(
            WorkflowSchema.workflow_id == values["workflow_id"],
            WorkflowSchema.sha256 == values["sha256"],
            WorkflowSchema.source == values["source"],
            WorkflowSchema.status == values["status"],
        )
        found = self.session.scalars(query.limit(1)).one_or_none()
        return found if found is not None else self.add_version(values)
