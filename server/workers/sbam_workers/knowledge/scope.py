"""Database side of the Cognee adapter scope checks (E5.1-T03), tenant-scoped repositories only."""

import uuid

import sbam_api.ingest.models  # noqa: F401 - registers DocumentVersion for the state machine
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.projects.repository import ProjectRepository
from sbam_contracts.knowledge import ProjectScope
from sqlalchemy.orm import Session, sessionmaker


class SqlScopeGuard:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def project_in_tenant(self, scope: ProjectScope) -> bool:
        with self._sessions() as session:
            repo = ProjectRepository(session, TenantContext(scope.tenant_id))
            return repo.get(scope.project_id) is not None

    def eligible_versions(self, scope: ProjectScope, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        with self._sessions() as session:
            ctx = TenantContext(scope.tenant_id, scope.project_id)
            return DocumentVersionRepository(session, ctx).parsed_clean_ids(sorted(ids))
