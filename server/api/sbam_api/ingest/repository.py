"""Tenant-scoped, project-scoped queries for uploads and documents (E4.2)."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.ingest.models import Document, DocumentVersion, ReservationStatus, UploadReservation
from sbam_api.state_machine.repository import StatefulRepository

# A rejected, deleted or purged version no longer takes one of the 20 slots.
_INACTIVE = (S.REJECTED.value, S.DELETED.value, S.PURGED.value)


class DocumentRepository(TenantScopedRepository[Document]):
    model = Document

    def get_for_update(self, id: uuid.UUID) -> Document | None:
        """Like `get(include_deleted=True)`, with the row locked: delete, restore and purge of the
        same document run one after another, never at the same time."""
        query = self._scoped(include_deleted=True).where(Document.id == id).with_for_update()
        return self.session.scalars(query).one_or_none()

    def listing(self, *, include_deleted: bool) -> list[Document]:
        """The project's documents, oldest first. Deleted ones only on request."""
        return self.list(include_deleted=include_deleted)


class DocumentVersionRepository(StatefulRepository):
    model = DocumentVersion

    def active_count(self) -> int:
        query = self._scoped().where(DocumentVersion.state.not_in(_INACTIVE))
        return self.session.scalar(select(func.count()).select_from(query.subquery())) or 0

    def active_ids(self) -> list[uuid.UUID]:
        """Ids of the versions that count towards the 20-file limit, oldest first (E4.1 start)."""
        query = self._scoped().where(DocumentVersion.state.not_in(_INACTIVE))
        return list(
            self.session.scalars(
                query.with_only_columns(DocumentVersion.id).order_by(DocumentVersion.id)
            )
        )

    def count_by_state(self) -> dict[str, int]:
        """Version count per state, deleted and purged excluded (E4.1 dashboard)."""
        query = (
            self._scoped()
            .where(DocumentVersion.state.not_in((S.DELETED.value, S.PURGED.value)))
            .with_only_columns(DocumentVersion.state, func.count())
            .group_by(DocumentVersion.state)
        )
        return {state: n for state, n in self.session.execute(query)}

    def by_sha256(self, sha256: str) -> DocumentVersion | None:
        query = self._scoped().where(
            DocumentVersion.sha256 == sha256, DocumentVersion.state.not_in(_INACTIVE)
        )
        return self.session.scalars(query.order_by(DocumentVersion.id).limit(1)).first()

    def others_using_key(self, storage_key: str, exclude: uuid.UUID) -> int:
        """Other versions (any state but REJECTED / PURGED) that point at this stored file. Deleted
        versions count: restoring one must still find its file."""
        query = self._scoped(include_deleted=True).where(
            DocumentVersion.storage_key == storage_key,
            DocumentVersion.id != exclude,
            DocumentVersion.state.not_in((S.REJECTED.value, S.PURGED.value)),
        )
        return self.session.scalar(select(func.count()).select_from(query.subquery())) or 0

    def of_documents(self, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, DocumentVersion]:
        """The version of each document (one file per document today), keyed by document id."""
        if not document_ids:
            return {}
        query = self._scoped().where(DocumentVersion.document_id.in_(document_ids))
        return {v.document_id: v for v in self.session.scalars(query.order_by(DocumentVersion.id))}

    def of_document(self, document_id: uuid.UUID) -> list[DocumentVersion]:
        """Every version of one document, oldest first (versions are never soft-deleted rows)."""
        query = self._scoped().where(DocumentVersion.document_id == document_id)
        return list(self.session.scalars(query.order_by(DocumentVersion.id)))

    def waiting_to_start(self) -> list[DocumentVersion]:
        """Registered but not yet handed to Temporal (it was down), oldest first."""
        query = self._scoped().where(
            DocumentVersion.state == S.UPLOADED.value,
            DocumentVersion.ingestion_started_at.is_(None),
        )
        return list(self.session.scalars(query.order_by(DocumentVersion.id)))


class ReservationRepository(TenantScopedRepository[UploadReservation]):
    model = UploadReservation

    def pending_count(self, now: datetime, exclude: uuid.UUID | None = None) -> int:
        """Files announced or started but not finished, still inside their hold."""
        query = self._scoped().where(
            UploadReservation.status.in_((ReservationStatus.RESERVED, ReservationStatus.CREATED)),
            UploadReservation.expires_at > now,
        )
        if exclude is not None:
            query = query.where(UploadReservation.id != exclude)
        return self.session.scalar(select(func.count()).select_from(query.subquery())) or 0

    def claim(
        self, batch_id: uuid.UUID, name: str, size: int, now: datetime
    ) -> UploadReservation | None:
        """The unused slot of this token's batch for a file of this name and size (row locked)."""
        query = (
            self._scoped()
            .where(
                UploadReservation.batch_id == batch_id,
                UploadReservation.name == name,
                UploadReservation.size == size,
                UploadReservation.status == ReservationStatus.RESERVED.value,
                UploadReservation.expires_at > now,
            )
            .order_by(UploadReservation.id)
            .limit(1)
            .with_for_update()
        )
        return self.session.scalars(query).first()

    def get_for_update(self, id: uuid.UUID) -> UploadReservation | None:
        query = self._scoped().where(UploadReservation.id == id).with_for_update()
        return self.session.scalars(query).one_or_none()


def due_for_purge(
    session: Session, as_of: datetime, limit: int, exclude: Sequence[uuid.UUID] = ()
) -> list[Document]:
    """System job only (the PurgeDeleted schedule): soft-deleted documents of every tenant whose
    `purge_after` has passed and that still have a DELETED version, oldest first. Never reachable
    from a request. Restored documents (deleted_at cleared) and purged ones are not returned.
    `exclude`: documents this run already failed on, so they do not starve the others."""
    deleted_versions = select(DocumentVersion.document_id).where(
        DocumentVersion.state == S.DELETED.value
    )
    query = (
        select(Document)
        .where(
            Document.deleted_at.is_not(None),
            Document.purge_after <= as_of,
            Document.id.in_(deleted_versions),
            Document.id.not_in(exclude),
        )
        .order_by(Document.id)
        .limit(limit)
    )
    return list(session.scalars(query))
