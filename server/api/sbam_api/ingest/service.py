"""Upload token, tusd pre-create and post-finish (E4.2, TD §5.2, §8.5).

Flow
  1. `request_upload`: the FDE announces 1..20 files. Each is checked (type, 15 MB, 20-file limit)
     and the accepted ones get an upload reservation. The answer carries a signed token.
  2. tusd `pre-create` -> `pre_create`: the token, the project, the size and the count are checked
     again, and the upload must match an unused reservation. The reservation id becomes the tus
     upload id.
  3. tusd `post-finish` -> `post_finish`: the staged file is stored through `StorageBackend`
     (content-addressed, sha256 recomputed), registered as a document version through the
     state-machine service, or linked to the identical version already in the project.

Every check runs on the server even if the UI already did it. Nothing here reads the uploaded
bytes except to hash them; parsing waits for the scan (E4.3).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import (
    MAX_ACTIVE_FILES,
    MAX_FILE_BYTES,
    UPLOAD_TOKEN_TTL_SECONDS,
    AcceptedFile,
    RejectedFile,
    UploadRequest,
    UploadToken,
)
from sbam_contracts.projects import ProjectStatus
from sbam_contracts.state_machines import Actor
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.ingest import tokens
from sbam_api.ingest.models import Document, DocumentVersion, ReservationStatus, UploadReservation
from sbam_api.ingest.repository import (
    DocumentRepository,
    DocumentVersionRepository,
    ReservationRepository,
)
from sbam_api.ingest.rules import UPLOAD_HOLD, is_allowed
from sbam_api.projects.repository import ProjectRepository
from sbam_api.projects.service import require_active
from sbam_api.state_machine import apply_create
from sbam_api.storage.base import Kind, StorageBackend, StorageScope

log = logging.getLogger("sbam_api.ingest")

_STATUS = {
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.FILE_LIMIT_REACHED: 409,
    ErrorCode.UNSUPPORTED_TYPE: 415,
}
_TITLE = {
    ErrorCode.FILE_TOO_LARGE: "File too large",
    ErrorCode.FILE_LIMIT_REACHED: "Too many files",
    ErrorCode.UNSUPPORTED_TYPE: "Unsupported file type",
}
_DETAIL = {
    ErrorCode.FILE_TOO_LARGE: f"A file can be at most {MAX_FILE_BYTES} bytes (15 MB).",
    ErrorCode.FILE_LIMIT_REACHED: f"A project can hold at most {MAX_ACTIVE_FILES} active files.",
    ErrorCode.UNSUPPORTED_TYPE: "This file type cannot be uploaded.",
}


def _correlation() -> str:
    return correlation_id_var.get() or "unknown"


def _audit(
    session: Session,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    entity_id: uuid.UUID,
    after: dict[str, Any],
    entity_type: str = "upload",
) -> None:
    record(
        session,
        tenant_id=tenant_id,
        actor_id=str(actor_id),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        after=after,
        correlation_id=_correlation(),
    )


def _limit_error(code: ErrorCode) -> ApiError:
    return ApiError(_STATUS[code], code, _TITLE[code], _DETAIL[code])


# --- POST /projects/{id}/uploads -------------------------------------------------------------


def request_upload(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    body: UploadRequest,
    secret: str,
    tus_endpoint: str,
    now: datetime | None = None,
) -> UploadToken:
    now = now or utcnow()
    ctx = user.context(project_id)
    project = ProjectRepository(session, user.context()).get(project_id)
    if project is None:
        raise not_found()
    require_active(project)

    used = DocumentVersionRepository(session, ctx).active_count()
    reservations = ReservationRepository(session, ctx)
    used += reservations.pending_count(now)
    batch_id = uuid7()
    accepted: list[AcceptedFile] = []
    rejected: list[RejectedFile] = []
    for file in body.files:
        code: ErrorCode | None = None
        if not is_allowed(file.name):
            code = ErrorCode.UNSUPPORTED_TYPE
        elif file.size > MAX_FILE_BYTES:
            code = ErrorCode.FILE_TOO_LARGE
        elif used + len(accepted) + 1 > MAX_ACTIVE_FILES:
            code = ErrorCode.FILE_LIMIT_REACHED
        if code is not None:
            rejected.append(RejectedFile(name=file.name, code=code))
            _audit(
                session,
                ctx.tenant_id,
                user.user_id,
                "UPLOAD_REJECTED",
                batch_id,
                {"name": file.name, "code": code.value},
            )
            continue
        accepted.append(AcceptedFile(name=file.name, size=file.size, mime=file.mime))

    if not accepted:  # nothing is stored; the first reason answers the request
        session.commit()  # the rejection audit rows stay
        raise _limit_error(rejected[0].code)

    expires_at = now + timedelta(seconds=UPLOAD_TOKEN_TTL_SECONDS)
    for slot in accepted:
        row = UploadReservation(
            batch_id=batch_id,
            user_id=user.user_id,
            name=slot.name,
            size=slot.size,
            mime=slot.mime,
            status=ReservationStatus.RESERVED.value,
            expires_at=expires_at,
            created_by=str(user.user_id),
        )
        reservations.add(row)
        _audit(
            session,
            ctx.tenant_id,
            user.user_id,
            "UPLOAD_ACCEPTED",
            row.id,
            {"name": slot.name, "size": slot.size},
        )
    session.commit()
    token = tokens.issue(
        secret,
        tokens.UploadClaims(
            tenant_id=ctx.tenant_id,
            project_id=project_id,
            user_id=user.user_id,
            batch_id=batch_id,
            expires_at=int(expires_at.timestamp()),
        ),
    )
    return UploadToken(
        token=token,
        tus_endpoint=tus_endpoint,
        expires_at=expires_at,
        accepted=accepted,
        rejected=rejected,
    )


# --- tusd hooks ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """The answer to a pre-create hook. `code is None` means accept."""

    code: ErrorCode | None = None
    status: int = 200
    detail: str = ""
    upload_id: str | None = None


def _deny(status: int, code: ErrorCode, detail: str) -> Decision:
    return Decision(code=code, status=status, detail=detail)


def pre_create(
    session: Session,
    secret: str,
    meta: dict[str, str],
    size: int | None,
    size_deferred: bool,
    now: datetime | None = None,
) -> Decision:
    now = now or utcnow()
    if size_deferred or size is None:
        return _deny(400, ErrorCode.VALIDATION_ERROR, "Declare the file size when you upload.")
    try:
        claims = tokens.verify(secret, meta.get("token", ""), now.timestamp())
    except tokens.TokenError as exc:
        log.info("upload refused: token %s", exc)
        return _deny(403, ErrorCode.FORBIDDEN, "This upload token is not valid.")
    ctx = TenantContext(claims.tenant_id, claims.project_id)

    def refuse(status: int, code: ErrorCode, detail: str) -> Decision:
        _audit(
            session,
            claims.tenant_id,
            claims.user_id,
            "UPLOAD_REJECTED",
            claims.batch_id,
            {"code": code.value, "reason": detail},
        )
        session.commit()
        return _deny(status, code, detail)

    if meta.get("project_id") != str(claims.project_id):  # token of another project (T08)
        return refuse(403, ErrorCode.FORBIDDEN, "This upload token is for another project.")
    project = ProjectRepository(session, TenantContext(claims.tenant_id)).get(claims.project_id)
    if project is None:
        return refuse(404, ErrorCode.NOT_FOUND, "Project not found.")
    if project.status == ProjectStatus.ARCHIVED:
        return refuse(409, ErrorCode.PROJECT_ARCHIVED, "This project is archived and read-only.")
    if size > MAX_FILE_BYTES:
        return refuse(413, ErrorCode.FILE_TOO_LARGE, _DETAIL[ErrorCode.FILE_TOO_LARGE])
    name = meta.get("filename", "")
    if not is_allowed(name):
        return refuse(415, ErrorCode.UNSUPPORTED_TYPE, _DETAIL[ErrorCode.UNSUPPORTED_TYPE])
    reservations = ReservationRepository(session, ctx)
    slot = reservations.claim(claims.batch_id, name, size, now)
    if slot is None or slot.user_id != claims.user_id:
        return refuse(
            403, ErrorCode.FORBIDDEN, "No upload was announced for this file name and size."
        )
    others = reservations.pending_count(now, exclude=slot.id)
    if DocumentVersionRepository(session, ctx).active_count() + others + 1 > MAX_ACTIVE_FILES:
        return refuse(409, ErrorCode.FILE_LIMIT_REACHED, _DETAIL[ErrorCode.FILE_LIMIT_REACHED])
    slot.status = ReservationStatus.CREATED.value
    slot.expires_at = now + UPLOAD_HOLD  # a started upload keeps its slot for a long upload
    session.commit()
    return Decision(upload_id=str(slot.id))


@dataclass(frozen=True)
class Registered:
    version_id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    duplicate: bool


def post_finish(
    session: Session,
    storage: StorageBackend,
    secret: str,
    staging_dir: Path,
    upload_id: str,
    meta: dict[str, str],
    offset: int,
    size: int | None,
) -> Registered | None:
    """Register a finished upload. None = nothing to register (unknown, not complete, replay of
    something already refused); the cases that matter are logged."""
    try:
        claims = tokens.verify(secret, meta.get("token", ""), check_expiry=False)
        slot_id = uuid.UUID(upload_id)
    except (tokens.TokenError, ValueError):
        log.warning("post-finish ignored: unknown upload")
        return None
    ctx = TenantContext(claims.tenant_id, claims.project_id)
    slot = ReservationRepository(session, ctx).get_for_update(slot_id)
    if slot is None or slot.user_id != claims.user_id or slot.batch_id != claims.batch_id:
        log.warning("post-finish ignored: no matching reservation")
        return None
    if slot.status in (ReservationStatus.FINISHED, ReservationStatus.DUPLICATE):  # a retried hook
        return _registered(slot, claims, duplicate=slot.status == ReservationStatus.DUPLICATE)
    if slot.status != ReservationStatus.CREATED or offset != slot.size or size != slot.size:
        log.warning("post-finish ignored: upload is not complete")
        return None
    staged = staging_dir / str(slot.id)  # the id is a UUID we chose: no path from the client
    if not staged.is_file() or staged.stat().st_size != slot.size:
        log.warning("post-finish ignored: staged file missing or wrong size")
        return None

    with staged.open("rb") as handle:
        stored = storage.put(
            handle,
            StorageScope(claims.tenant_id, claims.project_id),
            Kind.UPLOADS,
            slot.mime,
            max_bytes=MAX_FILE_BYTES,
        )
    versions = DocumentVersionRepository(session, ctx)
    existing = versions.by_sha256(stored.sha256)
    if existing is not None:  # same content already in the project: link it, store nothing new
        slot.status = ReservationStatus.DUPLICATE.value
        slot.document_version_id = existing.id
        _audit(
            session,
            claims.tenant_id,
            claims.user_id,
            "UPLOAD_DUPLICATE",
            slot.id,
            {"name": slot.name, "sha256": stored.sha256, "linked_to": str(existing.id)},
        )
    else:
        document = DocumentRepository(session, ctx).add(
            Document(title=slot.name, created_by=str(claims.user_id))
        )
        version = DocumentVersion(
            id=slot.id,  # natural key: a retried hook finds the row it created
            document_id=document.id,
            storage_key=stored.storage_key,
            sha256=stored.sha256,
            size=stored.size,
            mime=slot.mime,
            created_by=str(claims.user_id),
        )
        apply_create(
            session,
            ctx,
            version,
            event="upload",
            actor=Actor.FDE,
            actor_id=str(claims.user_id),
            correlation_id=_correlation(),
            idempotency_key=f"upload:{slot.id}",
        )
        slot.status = ReservationStatus.FINISHED.value
        slot.document_version_id = version.id
        _audit(
            session,
            claims.tenant_id,
            claims.user_id,
            "DOCUMENT_UPLOADED",
            version.id,
            {"name": slot.name, "sha256": stored.sha256, "size": stored.size},
            entity_type="document_version",
        )
    session.commit()
    for leftover in (staged, staged.with_name(staged.name + ".info")):
        leftover.unlink(missing_ok=True)  # best effort: the content now lives in storage
    return _registered(slot, claims, duplicate=existing is not None)


def _registered(
    slot: UploadReservation, claims: tokens.UploadClaims, *, duplicate: bool
) -> Registered:
    if slot.document_version_id is None:
        raise RuntimeError("a finished reservation has no document version")
    return Registered(
        slot.document_version_id, claims.tenant_id, claims.project_id, claims.user_id, duplicate
    )
