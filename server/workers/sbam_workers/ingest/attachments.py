"""E-mail attachments become child documents (E4.6, TD §5.2): `register_attachments` and
`finish_attachments`, the two activities `DocumentIngestion` runs after a PARSED e-mail.

`register_attachments_for` takes a PARSED `.eml` or `.msg` version, reads the stored message again
with its loader and, for each attachment in order:
  * the e-mail is itself an attachment (its document has a parent): TOO_DEEP, one level only;
  * the project already holds 20 active files: FILE_LIMIT_REACHED, nothing is created (the parent
    stays PARSED);
  * otherwise a child document and version are created through the state machine (`upload`, as the
    FDE who uploaded the e-mail), the bytes go through the StorageBackend, and the child's own
    `DocumentIngestion` is started by the workflow: it is scanned, type-checked and parsed like any
    upload, so an infected or unsupported attachment is REJECTED on its own.
What became of each attachment is written to the parent's `parse_report.attachments`. A child's id
is derived from the parent's version and the attachment (position and content), so a retried
activity finds the children it created.

`finish_attachments_for` runs when the children's workflows have ended and adds a warning to the
parent's report for each child that was rejected, failed to parse or could not be scanned. The
parent's state is never changed by a child.
Nothing here logs names or content of a document.
"""

import hashlib
import io
import logging
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache

import sbam_api.ingest.models  # noqa: F401 - registers DocumentVersion for the state machine
from sbam_api.audit.repository import record
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.ingest.repository import DocumentRepository, DocumentVersionRepository
from sbam_api.state_machine import apply_create
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_contracts.documents import (
    MAX_ACTIVE_FILES,
    MAX_ATTACHMENT_DEPTH,
    MAX_FILE_BYTES,
    AttachmentOutcome,
    AttachmentReport,
    ParseReport,
)
from sbam_contracts.pipeline import IdsResult, StageRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import Actor
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.ingest.guard import require_clean
from sbam_workers.ingest.loaders import Attachment, loader_for, no_heartbeat
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.attachments")

NOT_FOUND = "NotFound"  # the version or its file is gone: retrying cannot help
MAIL_TYPES = frozenset({"eml", "msg"})
_RESULT = "Attachment "  # the warnings of `finish_attachments`; the loader's start with lower case
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class AttachmentDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend


def child_id(parent_version: uuid.UUID, position: int, attachment: Attachment) -> uuid.UUID:
    """The id of the child version for this attachment of this e-mail (stable across retries)."""
    digest = hashlib.sha256(attachment.data).hexdigest()
    return uuid.uuid5(parent_version, f"{position}:{digest}")


def safe_name(name: str, position: int) -> str:
    """A file name without a path or control characters, at most 255 characters."""
    base = _CONTROL.sub("", re.split(r"[\\/]", name)[-1]).strip().strip(".") if name else ""
    return (base or f"attachment-{position + 1}")[:255]


def register_attachments_for(deps: AttachmentDeps, inp: StageRef) -> IdsResult:
    """The ids of the child versions to ingest (new and already existing), in the e-mail's order.
    Empty for a version that is not a PARSED e-mail."""
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        parent = DocumentVersionRepository(session, ctx).get(inp.ref_id)
        if parent is None:
            raise ApplicationError("document version not found", type=NOT_FOUND, non_retryable=True)
        require_clean(parent)
        if parent.state != S.PARSED.value or parent.detected_type not in MAIL_TYPES:
            return IdsResult(ids=[])
        key, kind = parent.storage_key, parent.detected_type
    try:
        data = deps.storage.get(key)
    except FileNotFoundError:
        raise ApplicationError(
            "stored file not found", type=NOT_FOUND, non_retryable=True
        ) from None
    loader = loader_for(kind)
    if loader is None:
        return IdsResult(ids=[])
    attachments = loader(data, no_heartbeat).attachments  # the same bytes parsed before
    if not attachments:
        return IdsResult(ids=[])
    return _register(deps, ctx, inp, attachments)


def _register(
    deps: AttachmentDeps, ctx: TenantContext, inp: StageRef, attachments: list[Attachment]
) -> IdsResult:
    scope = StorageScope(inp.tenant_id, inp.project_id)
    ids: list[uuid.UUID] = []
    reports: list[AttachmentReport] = []
    with deps.sessions() as session:
        versions = DocumentVersionRepository(session, ctx)
        documents = DocumentRepository(session, ctx)
        parent = versions.get(inp.ref_id)
        parent_doc = documents.get(parent.document_id) if parent else None
        if parent is None or parent_doc is None:
            raise ApplicationError("document not found", type=NOT_FOUND, non_retryable=True)
        depth = 1 if parent_doc.parent_document_id is not None else 0  # the e-mail's own depth
        for position, attachment in enumerate(attachments):
            name = safe_name(attachment.name, position)
            base = AttachmentReport(
                name=name,
                content_type=attachment.content_type[:255],
                size=len(attachment.data),
                outcome=AttachmentOutcome.REGISTERED,
            )
            vid = child_id(parent.id, position, attachment)
            existing = versions.get(vid)
            if existing is not None:  # a retry: the child was made the first time
                ids.append(vid)
                reports.append(base.model_copy(update={"document_id": existing.document_id}))
            elif depth + 1 > MAX_ATTACHMENT_DEPTH:
                reports.append(base.model_copy(update={"outcome": AttachmentOutcome.TOO_DEEP}))
            elif len(attachment.data) > MAX_FILE_BYTES:
                reports.append(base.model_copy(update={"outcome": AttachmentOutcome.TOO_LARGE}))
            elif versions.active_count() >= MAX_ACTIVE_FILES:
                outcome = AttachmentOutcome.FILE_LIMIT_REACHED
                reports.append(base.model_copy(update={"outcome": outcome}))
            else:
                stored = deps.storage.put(
                    io.BytesIO(attachment.data),
                    scope,
                    Kind.UPLOADS,
                    attachment.content_type[:255],
                    max_bytes=MAX_FILE_BYTES,
                )
                document = documents.add(
                    Document(
                        title=name,
                        parent_document_id=parent_doc.id,
                        created_by=parent_doc.created_by,
                    )
                )
                child = DocumentVersion(
                    id=vid,
                    document_id=document.id,
                    storage_key=stored.storage_key,
                    sha256=stored.sha256,
                    size=stored.size,
                    mime=attachment.content_type[:255],
                    created_by=parent_doc.created_by,
                )
                apply_create(
                    session,
                    ctx,
                    child,
                    event="upload",
                    actor=Actor.FDE,
                    actor_id=parent_doc.created_by or SYSTEM_ACTOR_ID,
                    correlation_id=inp.correlation_id,
                    idempotency_key=f"attachment:{vid}",
                )
                record(
                    session,
                    tenant_id=inp.tenant_id,
                    actor_id=SYSTEM_ACTOR_ID,
                    action="DOCUMENT_ATTACHMENT_ADDED",
                    entity_type="document_version",
                    entity_id=str(vid),
                    correlation_id=inp.correlation_id,
                    after={"parent": str(parent.id), "size": stored.size},
                )
                session.flush()  # the next attachment counts this one toward the 20 files
                ids.append(vid)
                reports.append(base.model_copy(update={"document_id": document.id}))
        _write_report(session, ctx, parent, reports, [])
        session.commit()
    return IdsResult(ids=ids)


def finish_attachments_for(deps: AttachmentDeps, inp: StageRef) -> IdsResult:
    """Once the children have been ingested: one warning on the parent's report for each child that
    did not end PARSED. Idempotent: the warnings of an earlier run are replaced."""
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        versions = DocumentVersionRepository(session, ctx)
        parent = versions.get(inp.ref_id)
        if parent is None or not parent.parse_report:
            return IdsResult(ids=[])
        report = ParseReport.model_validate(parent.parse_report)
        warnings: list[str] = []
        children: list[uuid.UUID] = []
        for item in report.attachments:
            if item.outcome is AttachmentOutcome.REGISTERED and item.document_id:
                child = versions.latest_for_document(item.document_id)
                if child is not None:
                    children.append(child.id)
                    warnings.extend(_problem(item.name, child))
            elif item.outcome is not AttachmentOutcome.REGISTERED:
                warnings.append(f"{_RESULT}{item.name!r} was not added: {_WHY[item.outcome]}")
        _write_report(session, ctx, parent, report.attachments, warnings)
        session.commit()
    return IdsResult(ids=children)


_WHY = {
    AttachmentOutcome.FILE_LIMIT_REACHED: "the project already holds 20 files",
    AttachmentOutcome.TOO_LARGE: "it is over 15 MB",
    AttachmentOutcome.TOO_DEEP: "only one level of attachments is followed",
}


def _problem(name: str, child: DocumentVersion) -> list[str]:
    if child.state == S.REJECTED.value:
        return [f"{_RESULT}{name!r} was rejected: {child.reject_code or 'REJECTED'}"]
    if child.state == S.PARSE_FAILED.value:
        return [f"{_RESULT}{name!r} could not be read: {child.parse_code or 'PARSE_ERROR'}"]
    if child.state == S.SCAN_FAILED.value:
        return [f"{_RESULT}{name!r} could not be scanned"]
    return []


def _write_report(
    session: Session,
    ctx: TenantContext,
    parent: DocumentVersion,
    attachments: list[AttachmentReport],
    result_warnings: list[str],
) -> None:
    """The parent's report with its attachments, and with `result_warnings` in place of any earlier
    ones from this step. The e-mail's other warnings are kept."""
    current = ParseReport.model_validate(parent.parse_report or {})
    kept = [w for w in current.warnings if not w.startswith(_RESULT)]
    updated = current.model_copy(
        update={"attachments": attachments, "warnings": [*kept, *result_warnings]}
    )
    parent.parse_report = updated.model_dump(mode="json")
    DocumentVersionRepository(session, ctx).update(parent, parent.row_version)


@lru_cache(maxsize=1)
def _deps() -> AttachmentDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    return AttachmentDeps(sessions=mirror.sessions(), storage=make_backend(settings))


@activity_for(TaskQueue.INGEST)
@activity.defn(name="register_attachments")
def register_attachments(inp: StageRef) -> IdsResult:
    return register_attachments_for(_deps(), inp)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="finish_attachments")
def finish_attachments(inp: StageRef) -> IdsResult:
    return finish_attachments_for(_deps(), inp)
