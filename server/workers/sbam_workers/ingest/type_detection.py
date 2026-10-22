"""The `detect_type` activity (E4.4, TD §5.2, §13.2): runs after a CLEAN scan, before any parser.

`detect_version` is the whole step and takes its dependencies as arguments, so tests run it with a
temp-dir storage and SQLite. The activity only builds the real dependencies.

For a version in state PARSING (the scan left it there) with a CLEAN scan:
  * the first `HEAD_BYTES` of the stored file, plus the ZIP entry list for `.docx`, `.xlsx` and
    `.zip`, go to `detect` (pure, `detect.py`);
  * detected: the type and its Filetype row are stored on the version, the version stays PARSING for
    the loader (E4.5 onward), and the file's id is handed on;
  * rejected: `type_rejected` (PARSING -> REJECTED) and the reason in `reject_code`
    (TYPE_MISMATCH, UNSUPPORTED_TYPE, INVALID_ENCODING), one transaction with the audit row and the
    notification; nothing is handed on.
A retry of the activity finds the answer already stored and repeats it. A version that was not
scanned CLEAN raises `NotScanned` (non-retryable): nothing reads an unscanned file.
Nothing here logs file names or content.
"""

import io
import logging
import uuid
import zipfile
from dataclasses import dataclass
from functools import lru_cache

import sbam_api.ingest.models  # noqa: F401 - registers DocumentVersion for the state machine
from sbam_api.audit.outbox import LogSink, dispatch_pending
from sbam_api.audit.repository import record
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import DocumentVersion
from sbam_api.ingest.repository import DocumentRepository, DocumentVersionRepository
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.factory import make_backend
from sbam_contracts.pipeline import IdsResult, StageRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import Actor
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.ingest.detect import HEAD_BYTES, Detected, detect
from sbam_workers.ingest.guard import require_clean
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.type_detection")

NOT_FOUND = "NotFound"  # the version, its document or its file is gone: retrying cannot help
NOT_DETECTABLE = "NotDetectable"  # the version is in a state that cannot be type-checked


@dataclass(frozen=True)
class DetectDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend


def _gone(what: str) -> ApplicationError:
    return ApplicationError(f"{what} not found", type=NOT_FOUND, non_retryable=True)


def detect_version(deps: DetectDeps, inp: StageRef) -> IdsResult:
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        session.expire_all()
        version = DocumentVersionRepository(session, ctx).get(inp.ref_id)
        if version is None:
            raise _gone("document version")
        require_clean(version)
        if version.state == S.REJECTED.value:  # an earlier attempt rejected it and committed
            return IdsResult(ids=[])
        if version.state != S.PARSING.value:
            raise ApplicationError(
                f"cannot detect the type of a version in state {version.state}",
                type=NOT_DETECTABLE,
                non_retryable=True,
            )
        if version.detected_type:  # an earlier attempt detected it and committed
            return IdsResult(ids=[version.id])
        document = DocumentRepository(session, ctx).get(version.document_id, include_deleted=True)
        if document is None:
            raise _gone("document")
        key, name = version.storage_key, document.title

    try:
        with deps.storage.open(key) as stream:
            head = stream.read(HEAD_BYTES)
        result = detect(name, head, lambda: _zip_names(deps.storage, key))
    except FileNotFoundError:
        raise _gone("stored file") from None

    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        if isinstance(result, Detected):
            version.detected_type = result.detected_type
            version.filetype_row_label = result.row_label
            DocumentVersionRepository(session, ctx).update(version, version.row_version)
            record(
                session,
                tenant_id=inp.tenant_id,
                actor_id=SYSTEM_ACTOR_ID,
                action="DOCUMENT_TYPE_DETECTED",
                entity_type="document_version",
                entity_id=str(version.id),
                correlation_id=inp.correlation_id,
                after={"detected_type": result.detected_type, "filetype": result.row_label},
            )
            session.commit()
            return IdsResult(ids=[version.id])
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=DocumentVersion,
                entity_id=version.id,
                event="type_rejected",
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=inp.correlation_id,
                idempotency_key=f"{inp.run_id}:type_rejected",
            ),
        )
        version = _load(session, ctx, inp.ref_id)
        version.reject_code = result.code.value
        DocumentVersionRepository(session, ctx).update(version, version.row_version)
        session.commit()
    _dispatch(deps.sessions)
    return IdsResult(ids=[])


def _load(session: Session, ctx: TenantContext, version_id: uuid.UUID) -> DocumentVersion:
    session.expire_all()
    version: DocumentVersion | None = DocumentVersionRepository(session, ctx).get(version_id)
    if version is None:
        raise _gone("document version")
    return version


def _zip_names(storage: StorageBackend, key: str) -> set[str]:
    """The archive's entry names. The whole file is read (at most 15 MB, the upload limit)."""
    return set(zipfile.ZipFile(io.BytesIO(storage.get(key))).namelist())


def _dispatch(sessions: sessionmaker[Session]) -> None:
    """After commit only; a failed dispatch leaves the notification pending."""
    try:
        dispatch_pending(sessions, LogSink())
    except Exception as exc:
        log.warning("notification dispatch failed", extra={"exc_type": type(exc).__name__})


@lru_cache(maxsize=1)
def _deps() -> DetectDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    return DetectDeps(sessions=mirror.sessions(), storage=make_backend(settings))


@activity_for(TaskQueue.INGEST)
@activity.defn(name="detect_type")
def detect_type(inp: StageRef) -> IdsResult:
    return detect_version(_deps(), inp)
