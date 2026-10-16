"""Soft delete, restore, retry and purge of documents (E4.9, E4.13).

Lifecycle
  delete   FDE deletes a document: its version goes UPLOADED/PARSED/... -> DELETED through the
           state machine, the document row gets `deleted_at`, `deleted_by` and
           `purge_after = deleted_at + 90 days`, and it disappears from the default list. Nothing
           is removed from storage yet.
  restore  Within the 90 days the FDE brings it back (version DELETED -> UPLOADED, so it is scanned
           and parsed again). It counts toward the 20-file limit again, so a full project answers
           409 FILE_LIMIT_REACHED. From `purge_after` on: 410 GONE.
  retry    A document whose scan or parse failed (SCAN_FAILED / PARSE_FAILED) goes back to SCANNING
           through the state machine and `DocumentIngestion` is started again for it (the router
           does that; if Temporal cannot be reached `undo_retry` puts it back). Any other state is
           409, so an infected (REJECTED) file can never be retried.
  purge    The daily `PurgeDeleted` Temporal schedule calls `purge_document` once per due document
           (version DELETED -> PURGED, system actor). The stored file, the derivatives and the
           knowledge references go away; the `documents` / `document_versions` rows stay as a
           tombstone, and the audit log keeps the history. Purging is idempotent: a retry after a
           partial failure finishes the job, and a document restored after it was selected is
           skipped (re-checked under the row lock).

Two things this module cannot do yet because their tables do not exist: knowing that a document is
used by a frozen workflow version, and removing derivatives / Cognee references. Both are narrow
ports (`DocumentUsage`, `PurgeCleanup`) with a no-op default, to be replaced by the modules that
own that data (E5 knowledge, E10 workflows).
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import (
    MAX_ACTIVE_FILES,
    PURGE_RETENTION_DAYS,
    DocumentOut,
    PurgeOutcome,
    PurgeTarget,
)
from sbam_contracts.state_machines import Actor, ProjectPipelineState
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy.orm import Session

from sbam_api.audit.repository import find_by_key, record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, locked, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.ingest.repository import (
    DocumentRepository,
    DocumentVersionRepository,
    ReservationRepository,
)
from sbam_api.projects.models import Project
from sbam_api.projects.repository import ProjectRepository
from sbam_api.projects.service import require_active
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition
from sbam_api.storage.base import StorageBackend

log = logging.getLogger("sbam_api.ingest")

RETENTION = timedelta(days=PURGE_RETENTION_DAYS)
# Once the SOW is approved the workflow was built from the documents as they were.
_APPROVED = frozenset({ProjectPipelineState.SOW_APPROVED, ProjectPipelineState.WORKFLOW_CREATED})


class DocumentUsage(Protocol):
    """Is this document an input of something frozen? (a certified / frozen workflow version)"""

    def is_frozen(self, session: Session, ctx: TenantContext, document_id: uuid.UUID) -> bool: ...


class NoUsage:
    """Default until workflows exist (E10): nothing can be frozen, so nothing is locked."""

    def is_frozen(self, session: Session, ctx: TenantContext, document_id: uuid.UUID) -> bool:
        return False


class PurgeCleanup(Protocol):
    """What a purge removes besides the uploaded file: derivatives and Cognee data references."""

    def derived_keys(
        self, session: Session, ctx: TenantContext, document_id: uuid.UUID
    ) -> Sequence[str]:
        """Storage keys of the files derived from this document (parsed text, chunks, ...)."""
        ...

    def remove_references(
        self, session: Session, ctx: TenantContext, document_id: uuid.UUID
    ) -> None:
        """Remove the document's data references from the knowledge store (Cognee)."""
        ...


class NoCleanup:
    """Default until knowledge (E5) stores derivatives and references: there are none to remove."""

    def derived_keys(
        self, session: Session, ctx: TenantContext, document_id: uuid.UUID
    ) -> Sequence[str]:
        return ()

    def remove_references(
        self, session: Session, ctx: TenantContext, document_id: uuid.UUID
    ) -> None:
        return None


def _correlation() -> str:
    return correlation_id_var.get() or "unknown"


def _aware(value: datetime) -> datetime:
    """SQLite drops the zone; Postgres keeps it. Everything here is UTC."""
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


def to_out(document: Document, version: DocumentVersion) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        title=document.title,
        state=S(version.state),
        size=version.size,
        mime=version.mime,
        sha256=version.sha256,
        created_at=document.created_at,
        deleted_at=document.deleted_at,
        deleted_by=document.deleted_by,
        purge_after=document.purge_after,
    )


def _audit(
    session: Session,
    ctx: TenantContext,
    actor_id: str,
    action: str,
    document: Document,
    after: dict[str, Any],
    correlation_id: str,
) -> None:
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=actor_id,
        action=action,
        entity_type="document",
        entity_id=str(document.id),
        after={"project_id": str(document.project_id), **after},
        correlation_id=correlation_id,
    )


def _active_project(session: Session, user: CurrentUser, project_id: uuid.UUID) -> Project:
    """The project, or 404; an archived project takes no changes (409 PROJECT_ARCHIVED)."""
    project: Project | None = ProjectRepository(session, user.context()).get(project_id)
    if project is None:
        raise not_found()
    return require_active(project)


# --- GET /projects/{id}/documents ----------------------------------------------------------


def list_documents(
    session: Session, user: CurrentUser, project_id: uuid.UUID, *, include_deleted: bool
) -> list[DocumentOut]:
    ctx = user.context(project_id)
    documents = DocumentRepository(session, ctx).listing(include_deleted=include_deleted)
    versions = DocumentVersionRepository(session, ctx).of_documents([d.id for d in documents])
    return [to_out(d, versions[d.id]) for d in documents if d.id in versions]


# --- DELETE /projects/{id}/documents/{document_id} -----------------------------------------


def delete_document(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    usage: DocumentUsage,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    ctx = user.context(project_id)
    project = _active_project(session, user, project_id)
    document = DocumentRepository(session, ctx).get_for_update(document_id)
    if document is None or document.deleted_at is not None:  # missing, other tenant/project, gone
        raise not_found()
    if usage.is_frozen(session, ctx, document.id):
        raise locked()
    correlation = _correlation()
    versions = DocumentVersionRepository(session, ctx).of_document(document.id)
    for version in versions:  # SCANNING / PARSING -> 409 INVALID_TRANSITION: wait for it to finish
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=DocumentVersion,
                entity_id=version.id,
                event="delete",
                actor=Actor.FDE,
                actor_id=str(user.user_id),
                correlation_id=correlation,
            ),
        )
    document.deleted_at = now
    document.deleted_by = str(user.user_id)
    document.purge_after = now + RETENTION
    DocumentRepository(session, ctx).update(document, document.row_version)
    context_changed = project.state in _APPROVED
    _audit(
        session,
        ctx,
        str(user.user_id),
        "DOCUMENT_DELETED",
        document,
        {
            "title": document.title,
            "purge_after": document.purge_after.isoformat(),
            # After SOW approval the workflow context changed; approved artifacts stay as they are.
            "workflow_context_changed": context_changed,
        },
        correlation,
    )
    session.commit()


# --- POST /projects/{id}/documents/{document_id}/restore -----------------------------------


def _gone() -> ApiError:
    return ApiError(
        410,
        ErrorCode.GONE,
        "Gone",
        f"This document was deleted more than {PURGE_RETENTION_DAYS} days ago and can no longer "
        "be restored.",
    )


def restore_document(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    now: datetime | None = None,
) -> DocumentOut:
    now = now or utcnow()
    ctx = user.context(project_id)
    _active_project(session, user, project_id)
    documents = DocumentRepository(session, ctx)
    document = documents.get_for_update(document_id)
    if document is None:
        raise not_found()
    if document.deleted_at is None:
        raise ApiError(409, ErrorCode.CONFLICT, "Not deleted", "This document is not deleted.")
    version_repo = DocumentVersionRepository(session, ctx)
    versions = version_repo.of_document(document.id)
    expired = document.purge_after is not None and now >= _aware(document.purge_after)
    if expired or any(v.state == S.PURGED.value for v in versions):
        raise _gone()
    deleted = [v for v in versions if v.state == S.DELETED.value]
    # Same count as the upload token: files in use plus uploads still in flight.
    used = version_repo.active_count() + ReservationRepository(session, ctx).pending_count(now)
    if used + len(deleted) > MAX_ACTIVE_FILES:
        raise ApiError(
            409,
            ErrorCode.FILE_LIMIT_REACHED,
            "Too many files",
            f"A project can hold at most {MAX_ACTIVE_FILES} active files.",
        )
    correlation = _correlation()
    for version in deleted:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=DocumentVersion,
                entity_id=version.id,
                event="restore",
                actor=Actor.FDE,
                actor_id=str(user.user_id),
                correlation_id=correlation,
            ),
        )
        version.ingestion_started_at = None  # restored files are scanned and parsed again
        version_repo.update(version, version.row_version)
    document.deleted_at = None
    document.deleted_by = None
    document.purge_after = None
    documents.update(document, document.row_version)
    _audit(
        session,
        ctx,
        str(user.user_id),
        "DOCUMENT_RESTORED",
        document,
        {"title": document.title, "knowledge_outdated": True},
        correlation,
    )
    session.commit()
    restored = version_repo.of_documents([document.id])
    return to_out(document, restored[document.id])


# --- POST /projects/{id}/documents/{document_id}/retry ------------------------------------

RETRYABLE = (S.SCAN_FAILED.value, S.PARSE_FAILED.value)


@dataclass(frozen=True)
class RetryResult:
    version_ids: list[uuid.UUID]
    replayed: bool  # the same Idempotency-Key was already served: nothing changed, start nothing


def retry_document(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    usage: DocumentUsage,
    idempotency_key: str | None = None,
) -> RetryResult:
    """Put the failed versions of a document back to SCANNING, in one transaction with the audit.

    The same `Idempotency-Key` again changes nothing (`replayed`); a version that is not failed is
    409 CONFLICT and a document in use by a frozen version is 423.
    """
    ctx = user.context(project_id)
    _active_project(session, user, project_id)
    documents = DocumentRepository(session, ctx)
    document = documents.get_for_update(document_id)
    if document is None or document.deleted_at is not None:  # missing, other tenant/project, gone
        raise not_found()
    if usage.is_frozen(session, ctx, document.id):
        raise locked()
    version_repo = DocumentVersionRepository(session, ctx)
    versions = version_repo.of_document(document.id)
    if idempotency_key and any(
        find_by_key(session, ctx.tenant_id, "document_version", str(v.id), idempotency_key)
        for v in versions
    ):
        return RetryResult([v.id for v in versions], replayed=True)
    failed = [v for v in versions if v.state in RETRYABLE]
    if not failed:
        raise ApiError(
            409, ErrorCode.CONFLICT, "Not failed", "Only failed documents can be retried."
        )
    correlation = _correlation()
    for version in failed:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=DocumentVersion,
                entity_id=version.id,
                event="retry",
                actor=Actor.FDE,
                actor_id=str(user.user_id),
                correlation_id=correlation,
                idempotency_key=idempotency_key,
            ),
        )
        version.ingestion_started_at = None  # the workflow is started again for it
        version_repo.update(version, version.row_version)
    _audit(
        session,
        ctx,
        str(user.user_id),
        "DOCUMENT_RETRIED",
        document,
        {"title": document.title, "from": sorted({v.state for v in failed})},
        correlation,
    )
    session.commit()
    return RetryResult([v.id for v in failed], replayed=False)


def undo_retry(
    session: Session, user: CurrentUser, project_id: uuid.UUID, version_ids: Sequence[uuid.UUID]
) -> None:
    """Temporal could not start the workflow: the versions go back to a failed, retryable state.

    `scan_unavailable` (SCANNING -> SCAN_FAILED, system) is the existing way to say "the scan did
    not happen"; a PARSE_FAILED document that comes back as SCAN_FAILED can be retried again.
    """
    ctx = user.context(project_id)
    correlation = _correlation()
    version_repo = DocumentVersionRepository(session, ctx)
    for version_id in version_ids:
        version = version_repo.get(version_id)
        if version is None or version.state != S.SCANNING.value:
            continue
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=DocumentVersion,
                entity_id=version_id,
                event="scan_unavailable",
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=correlation,
            ),
        )
    session.commit()


# --- PurgeDeleted: one document ------------------------------------------------------------


@dataclass(frozen=True)
class PurgeContext:
    """What the purge activity needs besides the database."""

    storage: StorageBackend
    cleanup: PurgeCleanup
    correlation_id: str


def purge_document(
    session: Session, target: PurgeTarget, as_of: datetime, purge: PurgeContext
) -> PurgeOutcome:
    """Purge one deleted document that is due at `as_of`. Safe to run any number of times.

    Order matters for retries: the bytes go first (a missing object is fine), the state change and
    its audit row are committed last and together. A storage error before the commit leaves the
    version DELETED, so the next attempt (or tomorrow's run) does the rest.
    """
    ctx = TenantContext(target.tenant_id, target.project_id)
    document = DocumentRepository(session, ctx).get_for_update(target.document_id)
    if document is None or document.deleted_at is None:  # gone, or restored after it was selected
        return PurgeOutcome.SKIPPED
    if document.purge_after is None or _aware(document.purge_after) > _aware(as_of):
        return PurgeOutcome.SKIPPED
    version_repo = DocumentVersionRepository(session, ctx)
    pending = [v for v in version_repo.of_document(document.id) if v.state == S.DELETED.value]
    if not pending:  # already purged
        return PurgeOutcome.SKIPPED

    keys: list[str] = list(purge.cleanup.derived_keys(session, ctx, document.id))
    for version in pending:
        # The same content uploaded again while this copy sat in the bin shares its stored file.
        if version_repo.others_using_key(version.storage_key, version.id) == 0:
            keys.append(version.storage_key)
    for key in dict.fromkeys(keys):
        purge.storage.delete(key)
    purge.cleanup.remove_references(session, ctx, document.id)

    for version in pending:
        result = apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=DocumentVersion,
                entity_id=version.id,
                event="purge",
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=purge.correlation_id,
                idempotency_key=f"purge:{version.id}",
            ),
        )
        if not result.replayed:
            _audit(
                session,
                ctx,
                SYSTEM_ACTOR_ID,
                "DOCUMENT_PURGED",
                document,
                {"title": document.title, "version_id": str(version.id)},
                purge.correlation_id,
            )
    session.commit()
    return PurgeOutcome.PURGED
