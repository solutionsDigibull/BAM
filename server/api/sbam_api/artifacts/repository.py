"""Tenant- and project-scoped `artifacts` access (E11.3).

`every_artifact` is the one cross-tenant query: the restore drill's hash check (E16.5, a system
job with no caller, `python -m sbam_api.artifacts.verify`) re-hashes every stored object. It is
read-only and never reachable from a request.
"""

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def every_artifact(session: Session, *, frozen_only: bool) -> Iterator[Artifact]:
    """Every tenant's artifacts (frozen ones only, or all), in id order, streamed."""
    query = select(Artifact).order_by(Artifact.id)
    if frozen_only:
        query = query.where(Artifact.is_frozen.is_(True))
    return session.scalars(query.execution_options(yield_per=500))
