"""Master documents (TD §7.3.2, E3.8): context files an Admin attaches to a master.

The steps of `attach`, each one before anything later can run:
  1. the project, when given, is the caller's and not archived (404 / 409);
  2. at most 15 MB (413 FILE_TOO_LARGE) and an extension with an MVP-enabled Filetype row
     (415 UNSUPPORTED_TYPE: a `.dwg` is a Future type, the allowlist is the Filetype configuration);
  3. the route: the row's `default_route`, or the Admin's choice (ADMIN_OVERRIDE). A YAML or JSON
     file that declares `openapi:` / `swagger:` is the OpenAPI / Swagger row (Agents), a Python
     file that imports `fastmcp` the FastMCP row; the intent class is that row's (README §9);
  4. the same bytes in the same scope (the tenant, or one project) are not stored twice: the
     existing document is answered (200). One whose scan never finished is scanned again;
  5. ClamAV scan of the raw bytes, with retries. Infected = the bytes go to `quarantine/`, the
     attempt is audited and the answer is 422 MALWARE; no document row exists, so nothing can be
     routed, listed or parsed. No verdict after the last attempt = the document is kept as
     SCAN_FAILED (upload it again to scan it again). Nothing is parsed before a CLEAN verdict;
  6. the bytes go to `uploads/` through `StorageBackend`, the row is written and audited.

Like the workbook import (E3.3) the scan runs in the request (a file is at most 15 MB and clamd
answers in milliseconds). Parsing the file into master context and handing it to Cognee is a
later step (it needs the loaders and the Cognee adapter) and starts from `parse_status = PENDING`.
The content is checked against its extension there, with the loader; here only the extension is.

A tenant-wide document (no project) is stored under the all-zero project id: storage keys are
`<tenant>/<project>/...`.

Nothing here logs file names or content.
"""

import hashlib
import io
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.documents import MAX_FILE_BYTES, ScanStatus
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_api import MasterDocumentOut, ParseStatus, RouteSource
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.bulkhead import CLAMD, BulkheadFullError
from sbam_api.core.conventions import ApiError, check_if_match, not_found
from sbam_api.core.settings import Settings
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantScopedRepository
from sbam_api.filetypes.config import Filetype, enabled_rows, load
from sbam_api.ingest.rules import extension
from sbam_api.masters.models import MasterDocument
from sbam_api.masters.repository import MasterEntryRepository
from sbam_api.masters.service import _active_project, _correlation
from sbam_api.scan.clamd import ClamdClient, ClamdError, ScanVerdict
from sbam_api.storage import Kind, StorageBackend, StorageScope

TENANT_SCOPE_PROJECT: Final = uuid.UUID(int=0)
SCAN_ATTEMPTS: Final = 5
SCAN_BACKOFF: Final = 0.5  # seconds, doubled after each failed attempt
SCAN_DEADLINE: Final = 10.0  # seconds a request may spend on retries before it takes SCAN_FAILED
SNIFF_BYTES: Final = 8192
_OPENAPI: Final = re.compile(r"""(?:^|[{,])\s*["']?openapi["']?\s*:""", re.MULTILINE)
_SWAGGER: Final = re.compile(r"""(?:^|[{,])\s*["']?swagger["']?\s*:""", re.MULTILINE)
_FASTMCP: Final = re.compile(r"^\s*(?:from|import)\s+fastmcp\b", re.MULTILINE)
_SPEC_ROWS: Final = {
    "openapi": "Filetype #15",
    "swagger": "Filetype #16",
    "fastmcp": "Filetype #13",
}


class MasterDocumentRepository(TenantScopedRepository[MasterDocument]):
    def pending_parse(self) -> list[MasterDocument]:
        """Scanned CLEAN and not parsed yet, oldest first (E3.8b: handed to Temporal)."""
        query = self._scoped().where(
            MasterDocument.scan_status == ScanStatus.CLEAN.value,
            MasterDocument.parse_status == ParseStatus.PENDING.value,
        )
        return list(self.session.scalars(query.order_by(MasterDocument.id)))

    model = MasterDocument

    def in_scope(self, project_id: uuid.UUID | None, sha256: str) -> MasterDocument | None:
        query = self._scoped().where(MasterDocument.sha256 == sha256)
        query = query.where(
            MasterDocument.project_id.is_(None)
            if project_id is None
            else MasterDocument.project_id == project_id
        )
        return self.session.scalars(query).first()

    def listing(
        self, master_type: MasterType | None, project_id: uuid.UUID | None
    ) -> list[MasterDocument]:
        query = self._scoped()
        if master_type is not None:
            query = query.where(MasterDocument.master_type == master_type.value)
        if project_id is not None:
            query = query.where(MasterDocument.project_id == project_id)
        return list(self.session.scalars(query.order_by(MasterDocument.id.desc())))


def _repo(session: Session, user: CurrentUser) -> MasterDocumentRepository:
    return MasterDocumentRepository(session, user.context())


def to_out(row: MasterDocument) -> MasterDocumentOut:
    return MasterDocumentOut(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        filetype_row_label=row.filetype_row_label,
        intent_class=row.intent_class,
        master_type=MasterType(row.master_type),  # type: ignore[arg-type]
        master_entry_id=row.master_entry_id,
        route_source=RouteSource(row.route_source),
        sha256=row.sha256,
        size=row.size,
        scan_status=ScanStatus(row.scan_status) if row.scan_status else None,
        parse_status=ParseStatus(row.parse_status),
        parse_code=ErrorCode(row.parse_code) if row.parse_code else None,
        created_at=row.created_at,
    )


# --- routing --------------------------------------------------------------------------------


def filetype_for(name: str, head: bytes) -> Filetype | None:
    """The Filetype row a file belongs to, or None when its extension has no MVP-enabled row."""
    ext = extension(name)
    row = enabled_rows().get(ext)
    if row is None:
        return None
    text = head[:SNIFF_BYTES].decode("utf-8", errors="replace")
    spec: str | None = None
    if ext in {"yaml", "yml", "json"}:
        spec = "openapi" if _OPENAPI.search(text) else "swagger" if _SWAGGER.search(text) else None
    elif ext == "py" and _FASTMCP.search(text):
        spec = "fastmcp"
    if spec is not None:
        label = _SPEC_ROWS[spec]
        return next((f for f in load() if f.row_label == label), row)
    return row


def _mime(row: Filetype) -> str:
    return row.config.magic[0] if row.config.magic else "application/octet-stream"


# --- attach ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Attached:
    document: MasterDocument
    created: bool  # False: the same file was already attached, this is that document


def _title(filename: str) -> str:
    name = re.split(r"[\\/]", filename.strip())[-1]
    return "".join(c for c in name if c.isprintable())[:255]


def attach(
    session: Session,
    user: CurrentUser,
    settings: Settings,
    storage: StorageBackend,
    filename: str,
    data: bytes,
    project_id: uuid.UUID | None,
    master_type: MasterType | None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Attached:
    if project_id is not None:
        _active_project(session, user, project_id)
    if len(data) > MAX_FILE_BYTES:
        raise ApiError(413, ErrorCode.FILE_TOO_LARGE, "The file is larger than 15 MB")
    title = _title(filename)
    if not title or not data:
        raise ApiError(
            422,
            ErrorCode.VALIDATION_ERROR,
            "A file with a name and content is required",
            errors=[FieldError(path="file", message="missing or empty")],
        )
    filetype = filetype_for(title, data)
    if filetype is None:
        raise ApiError(
            415,
            ErrorCode.UNSUPPORTED_TYPE,
            "This file type is not supported yet",
            errors=[FieldError(path="file", message=f"no loader for .{extension(title) or '?'}")],
        )

    sha256 = hashlib.sha256(data).hexdigest()
    repo = _repo(session, user)
    existing = repo.in_scope(project_id, sha256)
    if existing is not None and existing.scan_status == ScanStatus.CLEAN.value:
        return Attached(existing, created=False)  # linked, not duplicated

    verdict = _scan(settings, data, sleep)
    scope = StorageScope(user.context().tenant_id, project_id or TENANT_SCOPE_PROJECT)
    if verdict is not None and verdict.infected:
        stored = storage.put(
            data, scope, Kind.QUARANTINE, _mime(filetype), max_bytes=MAX_FILE_BYTES
        )
        record(
            session,
            tenant_id=user.tenant_id,
            actor_id=str(user.user_id),
            action="MASTER_DOCUMENT_QUARANTINED",
            entity_type="master_document",
            entity_id=sha256,
            after={"signature": verdict.signature, "quarantine_key": stored.storage_key},
            correlation_id=_correlation(),
        )
        if existing is not None:  # an earlier unscanned copy of the same bytes: gone with them
            repo.soft_delete(existing)
        session.commit()
        raise ApiError(422, ErrorCode.MALWARE, "The file contains malware and was quarantined")

    stored = storage.put(data, scope, Kind.UPLOADS, _mime(filetype), max_bytes=MAX_FILE_BYTES)
    status = ScanStatus.CLEAN if verdict is not None else ScanStatus.SCAN_FAILED
    default = MasterType(filetype.config.default_route.upper())
    chosen = master_type or default
    source = RouteSource.DEFAULT_BY_FILETYPE if chosen == default else RouteSource.ADMIN_OVERRIDE
    if existing is not None:  # SCAN_FAILED before: this attempt's verdict replaces it
        document = existing
        document.scan_status = status.value
        document.scanned_at = utcnow()
        action = "MASTER_DOCUMENT_RESCANNED"
    else:
        document = MasterDocument(
            project_id=project_id,
            title=title,
            filetype_row_label=filetype.row_label,
            intent_class=filetype.config.intent_class,
            master_type=chosen.value,
            route_source=source.value,
            storage_key=stored.storage_key,
            sha256=sha256,
            size=len(data),
            mime=_mime(filetype),
            scan_status=status.value,
            scanned_at=utcnow(),
            parse_status=ParseStatus.PENDING.value,
            created_by=str(user.user_id),
        )
        try:
            repo.add(document)
        except IntegrityError:  # the same file was attached by a request that finished first
            session.rollback()
            winner = _repo(session, user).in_scope(project_id, sha256)
            if winner is None:
                raise
            return Attached(winner, created=False)
        action = "MASTER_DOCUMENT_ATTACHED"
    record(
        session,
        tenant_id=user.tenant_id,
        actor_id=str(user.user_id),
        action=action,
        entity_type="master_document",
        entity_id=str(document.id),
        after={
            "project_id": str(project_id) if project_id else None,
            "filetype_row_label": document.filetype_row_label,
            "master_type": document.master_type,
            "route_source": document.route_source,
            "scan_status": status.value,
            "sha256": sha256,
        },
        correlation_id=_correlation(),
    )
    session.commit()
    return Attached(document, created=existing is None)


def _scan(settings: Settings, data: bytes, sleep: Callable[[float], None]) -> ScanVerdict | None:
    """The verdict, or None when clamd gave none in `SCAN_ATTEMPTS` tries (never a pass)."""
    clamd = ClamdClient(settings.clamav_host, settings.clamav_port, settings.clamav_timeout)
    delay = SCAN_BACKOFF
    started = time.monotonic()
    for attempt in range(SCAN_ATTEMPTS):
        try:
            with CLAMD.slot():
                return clamd.scan(io.BytesIO(data))
        except BulkheadFullError:
            raise scan_unavailable() from None
        except ClamdError:
            # The request gives up at SCAN_DEADLINE, not after every backoff of a hung clamd.
            if attempt == SCAN_ATTEMPTS - 1 or time.monotonic() - started + delay > SCAN_DEADLINE:
                break
            sleep(delay)
            delay *= 2
    return None


def scan_unavailable() -> ApiError:
    return ApiError(
        503,
        ErrorCode.SCAN_UNAVAILABLE,
        "The virus scanner is unavailable",
        "Nothing was imported. Try again in a few minutes.",
    )


# --- list, get, route -----------------------------------------------------------------------


def listing(
    session: Session,
    user: CurrentUser,
    master_type: MasterType | None,
    project_id: uuid.UUID | None,
) -> list[MasterDocument]:
    return _repo(session, user).listing(master_type, project_id)


def pending_parse(session: Session, user: CurrentUser) -> list[MasterDocument]:
    return _repo(session, user).pending_parse()


def get(session: Session, user: CurrentUser, document_id: uuid.UUID) -> MasterDocument:
    row = _repo(session, user).get(document_id)
    if row is None:
        raise not_found()
    return row


def reroute(
    session: Session,
    user: CurrentUser,
    document_id: uuid.UUID,
    master_type: MasterType,
    entry_id: uuid.UUID | None,
    if_match: str | None,
) -> MasterDocument:
    """Confirm or override the route (`If-Match` = the document's ETag)."""
    repo = _repo(session, user)
    row = repo.get(document_id)
    if row is None:
        raise not_found()
    check_if_match(if_match, row.row_version)
    if row.scan_status != ScanStatus.CLEAN.value:
        raise ApiError(409, ErrorCode.CONFLICT, "The file has not been scanned clean yet")
    if entry_id is not None:
        visible = {
            e.id
            for e in MasterEntryRepository(session, user.context()).of_type(
                master_type.value, row.project_id
            )
        }
        if entry_id not in visible:
            raise ApiError(
                422,
                ErrorCode.VALIDATION_ERROR,
                "Invalid master entry",
                errors=[
                    FieldError(
                        path="master_entry_id",
                        message=f"not an entry of {master_type.value} visible to this document",
                    )
                ],
            )
    if (row.master_type, row.master_entry_id) == (master_type.value, entry_id):
        return row  # already routed there: nothing to write
    before = {"master_type": row.master_type, "master_entry_id": _text(row.master_entry_id)}
    row.master_type = master_type.value
    row.master_entry_id = entry_id
    row.route_source = RouteSource.ADMIN_OVERRIDE.value
    repo.update(row, row.row_version)
    record(
        session,
        tenant_id=user.tenant_id,
        actor_id=str(user.user_id),
        action="MASTER_DOCUMENT_ROUTED",
        entity_type="master_document",
        entity_id=str(row.id),
        before=before,
        after={"master_type": master_type.value, "master_entry_id": _text(entry_id)},
        correlation_id=_correlation(),
    )
    session.commit()
    return row


def _text(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)
