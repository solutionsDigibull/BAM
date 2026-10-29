"""Tenant-scoped, project-scoped queries for uploads and documents (E4.2)."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sbam_contracts.documents import ScanStatus
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.ingest.models import Document, DocumentVersion, ReservationStatus, UploadReservation
from sbam_api.state_machine.repository import StatefulRepository

# A rejected, deleted or purged version no longer takes one of the 20 slots.
_INACTIVE = (S.REJECTED.value, S.DELETED.value, S.PURGED.value)


class DocumentRepository(TenantScopedRepository[Document]):
    model = Document

    def children(self, parent_document_id: uuid.UUID) -> list[Document]:
        """The documents made from an e-mail's attachments or an archive's members."""
        query = self._scoped().where(Document.parent_document_id == parent_document_id)
        return list(self.session.scalars(query.order_by(Document.id)))

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

    def latest_for_document(self, document_id: uuid.UUID) -> DocumentVersion | None:
        """The newest version of a document (E4.6: the state of an e-mail attachment)."""
        query = self._scoped().where(DocumentVersion.document_id == document_id)
        return self.session.scalars(query.order_by(DocumentVersion.id.desc()).limit(1)).first()

    def by_sha256(self, sha256: str) -> DocumentVersion | None:
        query = self._scoped().where(
            DocumentVersion.sha256 == sha256, DocumentVersion.state.not_in(_INACTIVE)
        )
        return self.session.scalars(query.order_by(DocumentVersion.id).limit(1)).first()

    def others_using_key(self, storage_key: str, exclude: uuid.UUID) -> int:
        """Other versions (any state but REJECTED / PURGED) that point at this stored file. Deleted
        versions count: restoring one must still find its file.

        Row-locked (`FOR UPDATE`) so two documents sharing a `storage_key` can never purge at the
        same time: whichever gets here first blocks the other until its PURGED transition commits,
        so the later call sees the up-to-date state instead of racing to both skip the delete.
        """
        query = (
            self._scoped(include_deleted=True)
            .where(
                DocumentVersion.storage_key == storage_key,
                DocumentVersion.id != exclude,
                DocumentVersion.state.not_in((S.REJECTED.value, S.PURGED.value)),
            )
            .with_for_update()
        )
        return len(self.session.scalars(query).all())

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

    def parsed_clean_ids(self, ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        """The ids among `ids` that are PARSED with a CLEAN scan: what may reach Cognee (E5.1)."""
        query = self._scoped().where(
            DocumentVersion.id.in_(list(ids)),
            DocumentVersion.state == S.PARSED.value,
            DocumentVersion.scan_status == ScanStatus.CLEAN.value,
        )
        return set(self.session.scalars(query.with_only_columns(DocumentVersion.id)))


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


def abandoned_reservations(
    session: Session,
    *,
    ended_by: datetime,
    ended_after: datetime | None = None,
    after: uuid.UUID | None = None,
    newest_first: bool = False,
    limit: int,
) -> list[UploadReservation]:
    """System job only (the CleanupAbandonedUploads schedule): reservations of every tenant that
    never finished (`RESERVED` or `CREATED`) and whose hold ended at or before `ended_by` (and,
    when given, after `ended_after`). By id from `after` on, oldest first or newest first. Never
    reachable from a request."""
    query = select(UploadReservation).where(
        UploadReservation.status.in_((ReservationStatus.RESERVED, ReservationStatus.CREATED)),
        UploadReservation.expires_at <= ended_by,
    )
    if ended_after is not None:
        query = query.where(UploadReservation.expires_at > ended_after)
    if after is not None:
        query = query.where(
            UploadReservation.id < after if newest_first else UploadReservation.id > after
        )
    order = UploadReservation.id.desc() if newest_first else UploadReservation.id
    return list(session.scalars(query.order_by(order).limit(limit)))


def reservation_by_id(session: Session, slot_id: uuid.UUID) -> UploadReservation | None:
    """System job only (the tusd post-finish hook): a reservation by its id, any tenant. The caller
    reads the tenant and project from the row and works through the scoped repositories from there.
    """
    return session.get(UploadReservation, slot_id)


def known_reservation_ids(session: Session, ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    """System job only: which of these ids are the id of an upload reservation (any tenant, any
    status). Used to tell a staged file that belongs to someone from an orphan."""
    if not ids:
        return set()
    return set(session.scalars(select(UploadReservation.id).where(UploadReservation.id.in_(ids))))


def delete_reservations(session: Session, ids: Sequence[uuid.UUID]) -> int:
    """System job only: delete abandoned reservations (never finished ones) by id; returns how
    many went. The status filter is repeated here so a mistake in the caller cannot delete the row
    that links a finished upload to its version."""
    if not ids:
        return 0
    result = session.execute(
        delete(UploadReservation).where(
            UploadReservation.id.in_(ids),
            UploadReservation.status.in_((ReservationStatus.RESERVED, ReservationStatus.CREATED)),
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
