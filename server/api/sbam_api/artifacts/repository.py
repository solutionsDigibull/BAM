"""Tenant- and project-scoped `artifacts` access (E11.3)."""

from sbam_api.artifacts.models import Artifact, ArtifactKind
from sbam_api.db.repository import TenantScopedRepository
from sbam_api.storage.base import StoredObject


class ArtifactRepository(TenantScopedRepository[Artifact]):
    model = Artifact

    def find(self, storage_key: str) -> Artifact | None:
        query = self._scoped().where(Artifact.storage_key == storage_key)
        return self.session.scalars(query).one_or_none()

    def index(self, kind: ArtifactKind, stored: StoredObject, created_by: str) -> Artifact:
        """One row per storage key: content-addressed storage already deduped the bytes."""
        found = self.find(stored.storage_key)
        if found is not None:
            return found
        return self.add(
            Artifact(
                kind=kind,
                storage_key=stored.storage_key,
                sha256=stored.sha256,
                size=stored.size,
                content_type=stored.content_type,
                created_by=created_by,
            )
        )
