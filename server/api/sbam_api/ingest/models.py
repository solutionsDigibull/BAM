"""`documents`, `document_versions` and `upload_reservations` (TD §7.4, issue E4.2).

An upload reservation is one file the FDE announced when asking for an upload token. It is what
the pre-create hook matches the upload against, and what counts toward the 20-file limit until the
upload finishes. Its id is the tusd upload id and, once registered, the document version's id.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sbam_contracts.documents import ScanStatus
from sbam_contracts.state_machines import MachineName
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, ProjectOwned, status_check
from sbam_api.state_machine import Stateful, state_check
from sbam_api.state_machine.registry import register_entity


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"  # token issued, tus upload not created yet
    CREATED = "CREATED"  # pre-create accepted, bytes are coming
    FINISHED = "FINISHED"  # registered as a new document version
    DUPLICATE = "DUPLICATE"  # same content already in the project: linked, nothing stored twice


class Document(ProjectOwned, Base):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String(255))  # the uploaded file's name
    # Soft delete (E4.9): `deleted_at` + `deleted_by` say when and by whom, `purge_after` is
    # deleted_at + 90 days, when the daily PurgeDeleted job removes the stored bytes.
    deleted_by: Mapped[str | None] = mapped_column(String(255))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Stateful, ProjectOwned, Base):
    """`state` changes only through the state-machine service."""

    __tablename__ = "document_versions"
    __table_args__ = (
        state_check(MachineName.DOCUMENT_VERSION),
        status_check(ScanStatus, column="scan_status"),
    )
    machine = MachineName.DOCUMENT_VERSION

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sbam.documents.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(255))  # <tenant>/<project>/uploads/<sha256>
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(String(255))
    # Set once the DocumentIngestion workflow is started; NULL = still to start (Temporal was down).
    ingestion_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The scan (E4.3): NULL until scanned. Nothing may read the file until it is CLEAN.
    scan_status: Mapped[str | None] = mapped_column(String(20))
    scan_signature_version: Mapped[str | None] = mapped_column(String(100))  # clamd VERSION
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why it is REJECTED: MALWARE (the scan), TYPE_MISMATCH, UNSUPPORTED_TYPE, INVALID_ENCODING.
    reject_code: Mapped[str | None] = mapped_column(String(40))
    # Type detection (E4.4), after a CLEAN scan: the type the content really is (an extension such
    # as "pdf" or "tar.gz") and the Filetype master row it maps to (e.g. "Filetype #21").
    detected_type: Mapped[str | None] = mapped_column(String(40))
    filetype_row_label: Mapped[str | None] = mapped_column(String(40))


class UploadReservation(ProjectOwned, Base):
    __tablename__ = "upload_reservations"
    __table_args__ = (status_check(ReservationStatus),)

    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # one token request
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=ReservationStatus.RESERVED.value)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # end of the hold
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # FINISHED / DUPLICATE


Index("ix_documents_purge_after", Document.purge_after)
Index("ix_document_versions_project_sha256", DocumentVersion.project_id, DocumentVersion.sha256)
Index(
    "ix_upload_reservations_project_status", UploadReservation.project_id, UploadReservation.status
)

register_entity(MachineName.DOCUMENT_VERSION, DocumentVersion)
