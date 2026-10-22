"""Tenant-scoped queries for SOWs and their versions (E6.2)."""

import uuid

from sqlalchemy import func

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.sow.models import Sow, SowVersion
from sbam_api.state_machine.repository import StatefulRepository


class SowRepository(TenantScopedRepository[Sow]):
    """Use with a context that names the project: its one `sows` row."""

    model = Sow

    def of_project(self, *, for_update: bool = False) -> Sow | None:
        query = self._scoped()
        if for_update:
            query = query.with_for_update()
        found: Sow | None = self.session.scalars(query).one_or_none()
        return found


class SowVersionRepository(StatefulRepository):
    model = SowVersion

    def by_run(self, run_id: uuid.UUID, knowledge_version_id: uuid.UUID) -> SowVersion | None:
        query = self._scoped().where(
            SowVersion.run_id == run_id, SowVersion.knowledge_version_id == knowledge_version_id
        )
        found: SowVersion | None = self.session.scalars(query).one_or_none()
        return found

    def by_number(self, sow_id: uuid.UUID, version: int) -> SowVersion | None:
        query = self._scoped().where(SowVersion.sow_id == sow_id, SowVersion.version == version)
        found: SowVersion | None = self.session.scalars(query).one_or_none()
        return found

    def of_sow(self, sow_id: uuid.UUID) -> list[SowVersion]:
        """Newest first."""
        query = self._scoped().where(SowVersion.sow_id == sow_id)
        return list(self.session.scalars(query.order_by(SowVersion.version.desc())))

    def next_number(self, sow_id: uuid.UUID) -> int:
        query = (
            self._scoped(include_deleted=True)
            .where(SowVersion.sow_id == sow_id)
            .with_only_columns(func.max(SowVersion.version))
        )
        return int(self.session.scalar(query) or 0) + 1
