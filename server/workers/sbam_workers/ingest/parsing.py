"""The `parse_document` activity (E4.5, TD §5.2, §13.2): runs after type detection.

`parse_version` is the whole step and takes its dependencies as arguments, so tests run it with a
temp-dir storage and SQLite. The activities only build the real dependencies.

For a version in state PARSING with a CLEAN scan and a detected type:
  * the loader of that type (`loaders.loader_for`) reads the stored file. A type with no loader
    yet leaves the version in PARSING and answers `ParseOutcome(parsed=False, code=None)`;
  * parsed: `doc.md` and `doc.json` are stored through the StorageBackend (each under its own
    content hash, so a rewrite is the same object), their keys and the report go on the version, and
    `parse_completed` (PARSING -> PARSED) is applied, one transaction with the audit row;
  * a file the loader cannot read (CORRUPT, ENCRYPTED, EMPTY, or a limit): `parse_failed`
    (PARSING -> PARSE_FAILED) with the code, no retry: the answer would be the same. The workflow
    goes on with the other documents.
An error that is not the file's (storage or database down) is raised and retried (3 attempts, 10 s
doubling). After the last attempt, or when the activity runs past its timeout, the workflow calls
`mark_parse_failed` (PARSE_ERROR or TIMEOUT), so a version never stays PARSING for good.
A retry of the activity finds the answer already stored and repeats it. A version that was not
scanned CLEAN raises `NotScanned` (non-retryable). Nothing here logs file names or content.
"""

import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache

import sbam_api.ingest.models  # noqa: F401 - registers DocumentVersion for the state machine
from pydantic import BaseModel
from sbam_api.audit.outbox import LogSink, dispatch_pending
from sbam_api.audit.repository import record
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import DocumentVersion
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import MAX_FILE_BYTES, ParsedDocument, ParseOutcome, ParseReport
from sbam_contracts.pipeline import StageRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import Actor
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.ingest.guard import require_clean
from sbam_workers.ingest.loaders import Heartbeat, Parsed, ParseError, loader_for, no_heartbeat
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.parsing")

NOT_FOUND = "NotFound"  # the version or its file is gone: retrying cannot help
NOT_PARSEABLE = "NotParseable"  # not in PARSING, or the type was never detected
PERMANENT = frozenset({NOT_FOUND, NOT_PARSEABLE, "NotScanned"})
# A derivative of a 15 MB file may be larger than the file (doc.json repeats the text with where
# each piece came from).
DERIVED_MAX_BYTES = MAX_FILE_BYTES * 8


@dataclass(frozen=True)
class ParseDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend


class ParseFailure(BaseModel):
    """Input of `mark_parse_failed`: the version and why the workflow gives up on it."""

    ref: StageRef
    code: ErrorCode


def _gone(what: str) -> ApplicationError:
    return ApplicationError(f"{what} not found", type=NOT_FOUND, non_retryable=True)


def _load(session: Session, ctx: TenantContext, version_id: uuid.UUID) -> DocumentVersion:
    session.expire_all()
    version: DocumentVersion | None = DocumentVersionRepository(session, ctx).get(version_id)
    if version is None:
        raise _gone("document version")
    return version


def _outcome(version: DocumentVersion) -> ParseOutcome:
    done = version.state == S.PARSED.value
    return ParseOutcome(
        document_version_id=version.id,
        parsed=done,
        code=ErrorCode(version.parse_code) if version.parse_code else None,
        report=ParseReport.model_validate(version.parse_report) if version.parse_report else None,
    )


def parse_version(deps: ParseDeps, inp: StageRef, beat: Heartbeat = no_heartbeat) -> ParseOutcome:
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        require_clean(version)
        if version.state in (S.PARSED.value, S.PARSE_FAILED.value):  # an earlier attempt finished
            return _outcome(version)
        if version.state != S.PARSING.value or not version.detected_type:
            raise ApplicationError(
                f"cannot parse a version in state {version.state}",
                type=NOT_PARSEABLE,
                non_retryable=True,
            )
        loader = loader_for(version.detected_type)
        if loader is None:  # no loader for this type yet: it stays PARSING
            return ParseOutcome(document_version_id=version.id, parsed=False)
        key, loader_id = version.storage_key, version.filetype_row_label or version.detected_type

    try:
        data = deps.storage.get(key)
    except FileNotFoundError:
        raise _gone("stored file") from None
    try:
        parsed = loader(data, beat)
    except ParseError as exc:
        return _fail(deps, ctx, inp, exc.code, ParseReport(warnings=[str(exc)[:200]]))

    document = ParsedDocument(
        document_version_id=inp.ref_id,
        loader=loader_id[:40],
        blocks=parsed.blocks,
        tables=parsed.tables,
    )
    scope = StorageScope(inp.tenant_id, inp.project_id)
    md = deps.storage.put(
        parsed.markdown.encode(), scope, Kind.DERIVED, "text/markdown", max_bytes=DERIVED_MAX_BYTES
    )
    js = deps.storage.put(
        document.model_dump_json(exclude_none=True).encode(),
        scope,
        Kind.DERIVED,
        "application/json",
        max_bytes=DERIVED_MAX_BYTES,
    )
    return _complete(deps, ctx, inp, parsed, md.storage_key, js.storage_key)


def _complete(
    deps: ParseDeps, ctx: TenantContext, inp: StageRef, parsed: Parsed, md_key: str, json_key: str
) -> ParseOutcome:
    report = parsed.report.model_copy(update={"tables": len(parsed.tables)})
    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        if version.state != S.PARSING.value:  # another attempt got here first
            return _outcome(version)
        _transition(session, ctx, inp, version.id, "parse_completed")
        version = _load(session, ctx, inp.ref_id)
        version.derived_md_key, version.derived_json_key = md_key, json_key
        version.parse_report = report.model_dump(mode="json")
        version.parse_code = None
        DocumentVersionRepository(session, ctx).update(version, version.row_version)
        record(
            session,
            tenant_id=inp.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action="DOCUMENT_PARSED",
            entity_type="document_version",
            entity_id=str(version.id),
            correlation_id=inp.correlation_id,
            after={
                "blocks": len(parsed.blocks),
                "tables": len(parsed.tables),
                "warnings": len(report.warnings),
            },
        )
        session.commit()
        outcome = _outcome(version)
    return outcome


def _fail(
    deps: ParseDeps, ctx: TenantContext, inp: StageRef, code: ErrorCode, report: ParseReport
) -> ParseOutcome:
    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        if version.state != S.PARSING.value:  # another attempt got here first
            return _outcome(version)
        _transition(session, ctx, inp, version.id, "parse_failed")
        version = _load(session, ctx, inp.ref_id)
        version.parse_code = code.value
        version.parse_report = report.model_dump(mode="json")
        DocumentVersionRepository(session, ctx).update(version, version.row_version)
        session.commit()
        outcome = _outcome(version)
    _dispatch(deps.sessions)
    return outcome


def mark_failed(deps: ParseDeps, failure: ParseFailure) -> ParseOutcome:
    """The workflow gives up (retries used up, or the timeout): the version is PARSE_FAILED."""
    inp = failure.ref
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        if version.state != S.PARSING.value:  # parsed or failed in the meantime: keep that answer
            return _outcome(version)
    return _fail(deps, ctx, inp, failure.code, ParseReport(warnings=[failure.code.value]))


def _transition(
    session: Session, ctx: TenantContext, inp: StageRef, version_id: uuid.UUID, event: str
) -> None:
    apply_transition(
        session,
        ctx,
        TransitionCommand(
            model=DocumentVersion,
            entity_id=version_id,
            event=event,
            actor=Actor.SYSTEM,
            actor_id=SYSTEM_ACTOR_ID,
            correlation_id=inp.correlation_id,
            idempotency_key=f"{inp.run_id}:{event}",
        ),
    )


def _dispatch(sessions: sessionmaker[Session]) -> None:
    """After commit only; a failed dispatch leaves the notification pending."""
    try:
        dispatch_pending(sessions, LogSink())
    except Exception as exc:
        log.warning("notification dispatch failed", extra={"exc_type": type(exc).__name__})


@lru_cache(maxsize=1)
def _deps() -> ParseDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    return ParseDeps(sessions=mirror.sessions(), storage=make_backend(settings))


@activity_for(TaskQueue.INGEST)
@activity.defn(name="parse_document")
def parse_document(inp: StageRef) -> ParseOutcome:
    return parse_version(_deps(), inp, activity.heartbeat)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="mark_parse_failed")
def mark_parse_failed(failure: ParseFailure) -> ParseOutcome:
    return mark_failed(_deps(), failure)
