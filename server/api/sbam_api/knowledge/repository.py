"""Tenant-scoped queries for knowledge versions and their sources (E5.2)."""

import uuid

from sbam_contracts.state_machines import KnowledgeVersionState
from sqlalchemy import func

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.knowledge.models import KnowledgeSource, KnowledgeVersion
from sbam_api.state_machine.repository import StatefulRepository


class KnowledgeVersionRepository(StatefulRepository):
    """Use with a context that names the project: every query is then that project's only."""

    model = KnowledgeVersion

    def by_run(self, run_id: uuid.UUID) -> KnowledgeVersion | None:
        query = self._scoped().where(KnowledgeVersion.run_id == run_id)
        found: KnowledgeVersion | None = self.session.scalars(query).one_or_none()
        return found

    def in_state(self, state: KnowledgeVersionState) -> list[KnowledgeVersion]:
        query = self._scoped().where(KnowledgeVersion.state == state.value)
        return list(self.session.scalars(query.order_by(KnowledgeVersion.version)))

    def next_number(self) -> int:
        query = self._scoped(include_deleted=True).with_only_columns(
            func.max(KnowledgeVersion.version)
        )
        return int(self.session.scalar(query) or 0) + 1

    def latest(self) -> KnowledgeVersion | None:
        query = self._scoped().order_by(KnowledgeVersion.version.desc()).limit(1)
        found: KnowledgeVersion | None = self.session.scalars(query).first()
        return found


class KnowledgeSourceRepository(TenantScopedRepository[KnowledgeSource]):
    model = KnowledgeSource

    def of_version(self, knowledge_version_id: uuid.UUID) -> list[KnowledgeSource]:
        query = self._scoped().where(KnowledgeSource.knowledge_version_id == knowledge_version_id)
        return list(self.session.scalars(query.order_by(KnowledgeSource.document_version_id)))

    def find(
        self, knowledge_version_id: uuid.UUID, document_version_id: uuid.UUID
    ) -> KnowledgeSource | None:
        query = self._scoped().where(
            KnowledgeSource.knowledge_version_id == knowledge_version_id,
            KnowledgeSource.document_version_id == document_version_id,
        )
        found: KnowledgeSource | None = self.session.scalars(query).one_or_none()
        return found
