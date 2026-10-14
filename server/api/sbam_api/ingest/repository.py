"""Tenant-scoped, project-scoped queries for uploads and documents (E4.2)."""

import uuid
from datetime import datetime

from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy import func, select

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.ingest.models import Document, DocumentVersion, ReservationStatus, UploadReservation
from sbam_api.state_machine.repository import StatefulRepository

# A rejected, deleted or purged version no longer takes one of the 20 slots.
_INACTIVE = (S.REJECTED.value, S.DELETED.value, S.PURGED.value)


class DocumentRepository(TenantScopedRepository[Document]):
    model = Document


class DocumentVersionRepository(StatefulRepository):
    model = DocumentVersion

    def active_count(self) -> int:
        query = self._scoped().where(DocumentVersion.state.not_in(_INACTIVE))
        return self.session.scalar(select(func.count()).select_from(query.subquery())) or 0

    def by_sha256(self, sha256: str) -> DocumentVersion | None:
        query = self._scoped().where(
            DocumentVersion.sha256 == sha256, DocumentVersion.state.not_in(_INACTIVE)
        )
        return self.session.scalars(query.order_by(DocumentVersion.id).limit(1)).first()

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
