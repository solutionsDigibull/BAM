"""The `parse_master_document` activity (E3.8b, TD §5.2): a master document is parsed like a file.

An Admin's master document (`master_documents`) is scanned in the request (E3.8) and lands with
`parse_status = PENDING`. `MasterDocumentIngestion` then runs this activity once for it:
  * a document that was not scanned CLEAN is never read (`NotScanned`, not retried);
  * the type is detected from the content, with the same check as an FDE upload (`detect`): a
    mismatch, an unsupported type or a bad encoding is FAILED with that code;
  * the loader of that type reads it with the same limits as an upload (15 MB, the loader caps);
    `doc.md` and `doc.json` are stored through the StorageBackend under their own content hash;
  * PARSED with the derivative keys and the loader's report, or FAILED with the loader's code. A
    master document is one file: an e-mail's attachments and an archive's members are listed in its
    `doc.md` but never made into documents, and a file with no text layer (a scan, an image) is
    FAILED `EMPTY` because the vision model is not run for master context (a later story).
An error that is not the file's is raised and retried; after the last attempt, or at the timeout,
`mark_master_document_failed` gives it a code, so a document never stays PARSING for good. A retry
finds the answer already stored. Nothing here logs file names or content.
"""

import io
import logging
import uuid
import zipfile
from dataclasses import dataclass
from functools import lru_cache

from sbam_api.audit.repository import record
from sbam_api.db.repository import TenantContext
from sbam_api.masters.documents import MasterDocumentRepository
from sbam_api.masters.models import MasterDocument
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import ParseReport, ScanStatus
from sbam_contracts.masters_api import ParseStatus
from sbam_contracts.pipeline import IdsResult, StageRef
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.ingest.detect import HEAD_BYTES, Detected, detect
from sbam_workers.ingest.loaders import Heartbeat, ParseError, loader_for, no_heartbeat
from sbam_workers.ingest.parsing import DERIVED_MAX_BYTES, ParseFailure, _json_file, _markdown_file
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.master_documents")

NOT_FOUND = "NotFound"  # the document or its file is gone: retrying cannot help
NOT_SCANNED = "NotScanned"  # nothing reads a file that was not scanned CLEAN
PERMANENT = frozenset({NOT_FOUND, NOT_SCANNED})
_DONE = (ParseStatus.PARSED.value, ParseStatus.FAILED.value)


@dataclass(frozen=True)
class MasterDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend


def _gone(what: str) -> ApplicationError:
    return ApplicationError(f"{what} not found", type=NOT_FOUND, non_retryable=True)


def _load(session: Session, ctx: TenantContext, document_id: uuid.UUID) -> MasterDocument:
    session.expire_all()
    row: MasterDocument | None = MasterDocumentRepository(session, ctx).get(document_id)
    if row is None:
        raise _gone("master document")
    return row


def _outcome(row: MasterDocument) -> IdsResult:
    return IdsResult(ids=[row.id] if row.parse_status == ParseStatus.PARSED.value else [])


def parse_master_document_for(
    deps: MasterDeps, inp: StageRef, beat: Heartbeat = no_heartbeat
) -> IdsResult:
    """The id of the document when it ends PARSED, nothing when it ends FAILED."""
    ctx = TenantContext(inp.tenant_id)
    with deps.sessions() as session:
        row = _load(session, ctx, inp.ref_id)
        if row.scan_status != ScanStatus.CLEAN.value:
            raise ApplicationError(
                "the master document was not scanned clean", type=NOT_SCANNED, non_retryable=True
            )
        if row.parse_status in _DONE:  # an earlier attempt finished
            return _outcome(row)
        row.parse_status = ParseStatus.PARSING.value
        MasterDocumentRepository(session, ctx).update(row, row.row_version)
        session.commit()
        key, name = row.storage_key, row.title

    try:
        data = deps.storage.get(key)
    except FileNotFoundError:
        raise _gone("stored file") from None
    found = detect(name, data[:HEAD_BYTES], lambda: _zip_names(data))
    if not isinstance(found, Detected):
        return _fail(deps, ctx, inp, found.code, ParseReport(warnings=[found.code.value]))
    loader = loader_for(found.detected_type)
    if loader is None:
        return _fail(deps, ctx, inp, ErrorCode.UNSUPPORTED_TYPE, ParseReport())
    try:
        parsed = loader(data, beat)
    except ParseError as exc:
        return _fail(deps, ctx, inp, exc.code, ParseReport(warnings=[str(exc)[:200]]))
    if parsed.report.ocr_needed_pages:
        warning = "the file has no text layer; master documents are not read by the vision model"
        return _fail(deps, ctx, inp, ErrorCode.EMPTY, ParseReport(warnings=[warning]))

    scope = StorageScope(inp.tenant_id, inp.project_id)
    with _markdown_file(parsed.markdown, beat) as md_file:
        md = deps.storage.put(
            md_file, scope, Kind.DERIVED, "text/markdown", max_bytes=DERIVED_MAX_BYTES
        )
    with _json_file(inp.ref_id, found.row_label, parsed, beat) as (json_file, blocks):
        js = deps.storage.put(
            json_file, scope, Kind.DERIVED, "application/json", max_bytes=DERIVED_MAX_BYTES
        )
    report = parsed.report.model_copy(update={"tables": len(parsed.tables)})
    with deps.sessions() as session:
        row = _load(session, ctx, inp.ref_id)
        if row.parse_status in _DONE:  # another attempt got here first
            return _outcome(row)
        row.parse_status = ParseStatus.PARSED.value
        row.parse_code = None
        row.parse_report = report.model_dump(mode="json")
        row.derived_md_key, row.derived_json_key = md.storage_key, js.storage_key
        MasterDocumentRepository(session, ctx).update(row, row.row_version)
        record(
            session,
            tenant_id=inp.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action="MASTER_DOCUMENT_PARSED",
            entity_type="master_document",
            entity_id=str(row.id),
            correlation_id=inp.correlation_id,
            after={"blocks": blocks, "tables": len(parsed.tables), "loader": found.row_label},
        )
        session.commit()
        return _outcome(row)


def _fail(
    deps: MasterDeps, ctx: TenantContext, inp: StageRef, code: ErrorCode, report: ParseReport
) -> IdsResult:
    with deps.sessions() as session:
        row = _load(session, ctx, inp.ref_id)
        if row.parse_status in _DONE:  # another attempt got here first
            return _outcome(row)
        row.parse_status = ParseStatus.FAILED.value
        row.parse_code = code.value
        row.parse_report = report.model_dump(mode="json")
        MasterDocumentRepository(session, ctx).update(row, row.row_version)
        record(
            session,
            tenant_id=inp.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action="MASTER_DOCUMENT_PARSE_FAILED",
            entity_type="master_document",
            entity_id=str(row.id),
            correlation_id=inp.correlation_id,
            after={"code": code.value},
        )
        session.commit()
        return _outcome(row)


def mark_failed_for(deps: MasterDeps, failure: ParseFailure) -> IdsResult:
    """The workflow gives up (retries used up, or the timeout): the document is FAILED."""
    inp = failure.ref
    ctx = TenantContext(inp.tenant_id)
    with deps.sessions() as session:
        row = _load(session, ctx, inp.ref_id)
        if row.parse_status in _DONE:  # parsed or failed in the meantime: keep that answer
            return _outcome(row)
    return _fail(deps, ctx, inp, failure.code, ParseReport(warnings=[failure.code.value]))


def _zip_names(data: bytes) -> set[str]:
    return set(zipfile.ZipFile(io.BytesIO(data)).namelist())


@lru_cache(maxsize=1)
def _deps() -> MasterDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    return MasterDeps(sessions=mirror.sessions(), storage=make_backend(settings))


@activity_for(TaskQueue.INGEST)
@activity.defn(name="parse_master_document")
def parse_master_document(inp: StageRef) -> IdsResult:
    return parse_master_document_for(_deps(), inp, activity.heartbeat)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="mark_master_document_failed")
def mark_master_document_failed(failure: ParseFailure) -> IdsResult:
    return mark_failed_for(_deps(), failure)
