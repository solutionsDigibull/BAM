"""Signed access to an uploaded original (#895): the link, its signature and the release rule.

The signature is an HMAC-SHA256 over tenant, project, document and expiry, keyed by a key derived
from `STORAGE_SIGNING_KEY` with its own context (a link for the OKF bundle or an export cannot be
replayed here, and the other way round). The link lives `ACCESS_TTL` seconds. It is the credential
for `GET /document-access/{id}`, which re-checks the document on every download, so a file deleted
or quarantined after the link was issued is refused too, whatever the expiry says.
"""

import hashlib
import hmac
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlencode

from sbam_contracts.api import API_BASE, ErrorCode
from sbam_contracts.documents import DocumentAccess
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.ingest.repository import DocumentRepository, DocumentVersionRepository
from sbam_api.ingest.rules import is_downloadable
from sbam_api.storage.base import StorageUnavailable
from sbam_api.storage.signing import MIN_KEY_LENGTH

log = logging.getLogger("sbam_api.ingest")

ACCESS_TTL: Final = 300  # seconds, the same as the other signed links (exports)
_CONTEXT: Final = b"sbam-document-access-v1"
_LINK_ERROR: Final = "Download link is invalid or has expired"


def _key(secret: str | None) -> bytes:
    # Never sign with a guessable key: empty, or short like the `change-me-storage` placeholder.
    if not secret or len(secret) < MIN_KEY_LENGTH:
        raise StorageUnavailable()
    return hmac.new(secret.encode(), _CONTEXT, hashlib.sha256).digest()


def sign(
    secret: str | None,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    expires: int,
) -> str:
    message = f"{tenant_id}\n{project_id}\n{document_id}\n{expires}".encode()
    return hmac.new(_key(secret), message, hashlib.sha256).hexdigest()


def verify(
    secret: str | None,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    expires: int,
    signature: str,
    now: float | None = None,
) -> None:
    """403 for an expired, tampered or foreign link; the same answer for all three."""
    expected = sign(secret, tenant_id, project_id, document_id, expires)
    current = time.time() if now is None else now
    if expires < current or not hmac.compare_digest(expected, signature):
        raise ApiError(403, ErrorCode.FORBIDDEN, _LINK_ERROR)


def _why_not(document: Document, version: DocumentVersion) -> ApiError | None:
    """Why the original is not open to the FDE; None when it is released (clean, not deleted)."""
    if version.state == S.PURGED.value:
        return ApiError(
            410, ErrorCode.GONE, "Gone", "This file was permanently deleted and cannot be opened."
        )
    if document.deleted_at is not None or version.state == S.DELETED.value:
        detail = "This file is in the recycle bin. Restore it to open it."
    elif version.state == S.REJECTED.value or version.scan_status == "INFECTED":
        detail = "This file was blocked by the scan and is quarantined. It cannot be opened."
    elif is_downloadable(version.state, version.scan_status):
        return None
    elif version.state == S.SCAN_FAILED.value:
        detail = "The scan of this file did not finish. Retry the scan to open it."
    else:
        detail = "This file is still being checked. It can be opened once the scan is clean."
    return ApiError(409, ErrorCode.DOCUMENT_NOT_RELEASED, "Document not released", detail)


def released_document(
    session: Session, ctx: TenantContext, document_id: uuid.UUID
) -> tuple[Document, DocumentVersion]:
    """The document and its newest version, or 404 (missing, other tenant or project) or the
    reason it is not open. Always tenant- and project-scoped."""
    document = DocumentRepository(session, ctx).get(document_id, include_deleted=True)
    if document is None:
        raise not_found()
    version = DocumentVersionRepository(session, ctx).latest_for_document(document.id)
    if version is None:
        raise not_found()
    problem = _why_not(document, version)
    if problem is not None:
        raise problem
    return document, version


def _download_name(title: str) -> str:
    return title.strip() or "download"


def issue_access(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    secret: str | None,
    now: float | None = None,
) -> DocumentAccess:
    """A short-lived link for one released document, audited (who, which document, never the
    content)."""
    ctx = user.context(project_id)
    document, version = released_document(session, ctx, document_id)
    issued = int(time.time() if now is None else now)
    expires = issued + ACCESS_TTL
    query = {
        "t": str(ctx.tenant_id),
        "p": str(project_id),
        "exp": expires,
        "sig": sign(secret, ctx.tenant_id, project_id, document.id, expires),
    }
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action="DOCUMENT_ACCESS_ISSUED",
        entity_type="document",
        entity_id=str(document.id),
        after={"project_id": str(project_id), "title": document.title, "expires_at": expires},
        correlation_id=correlation_id_var.get() or "unknown",
    )
    session.commit()
    return DocumentAccess(
        url=f"{API_BASE}/document-access/{document.id}?{urlencode(query)}",
        expires_at=datetime.fromtimestamp(expires, UTC),
        filename=_download_name(document.title),
        mime=version.mime,
    )
