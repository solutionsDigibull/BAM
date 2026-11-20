"""The `scan_and_import_masters` activity (#609, N-02b option (a)): clamd scan, parse and write of a
masters workbook or a master document, off the request thread.

The API stored the bytes unscanned under the scan's own storage scope and wrote
`MASTERS_SCAN_ACCEPTED` (`sbam_api.masters.scans`). This activity then runs the same code the
synchronous routes run, `masters.importer.import_workbook` and `masters.documents.attach`, so the
result, the refusals and the audit rows of today are unchanged:

  * a refusal the file earns (infected = `MALWARE` and quarantine, `STRUCTURE_MISMATCH`,
    `UNSUPPORTED_TYPE`, `FILE_TOO_LARGE`, ...) ends the scan FAILED with that problem. It is stored
    as `MASTERS_SCAN_FAILED`, and the activity returns normally: the answer would not change;
  * a failure that is not the file's (clamd down or the bulkhead full, storage unavailable: a 503)
    is raised, so Temporal retries it with backoff. After the last attempt the workflow calls
    `fail_masters_scan`: FAILED with `SCAN_UNAVAILABLE`, "nothing was imported";
  * success is stored as `MASTERS_SCAN_DONE`: an IMPORT's `ImportReport` as a JSON file in the
    scan's scope, a DOCUMENT's id and whether it is new. A retry finds the outcome and returns it.

The bytes are removed from storage once an outcome is stored. Nothing here logs file names or
content.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sbam_api.audit.repository import entity_history, record
from sbam_api.core.auth import ROLE_ADMIN, CurrentUser
from sbam_api.core.conventions import ApiError
from sbam_api.core.logging import correlation_id_var
from sbam_api.masters import documents, importer
from sbam_api.masters.scans import (
    DONE,
    ENTITY,
    FAILED,
    OUTCOMES,
    TENANT_SCOPE_PROJECT,
)
from sbam_api.scan.clamd import ClamdSettings
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import ScanStatus
from sbam_contracts.masters_api import ParseStatus
from sbam_contracts.masters_scan import (
    MastersScanInput,
    MastersScanKind,
    MastersScanResult,
    MastersScanState,
)
from sbam_contracts.runs import TaskQueue
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.masters_scan")

NOT_FOUND = "NotFound"  # the stored bytes are gone: retrying cannot bring them back
UNAVAILABLE = "ScanUnavailable"  # clamd, the bulkhead or storage said no: retry with backoff
PERMANENT = frozenset({NOT_FOUND})
OUTCOME_KEY = "outcome"  # the audit row's idempotency key: one outcome per scan


@dataclass(frozen=True)
class ClamdAt:
    clamav_host: str
    clamav_port: int
    clamav_timeout: float


@dataclass(frozen=True)
class ScanDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend
    clamd: ClamdSettings


def _user(inp: MastersScanInput) -> CurrentUser:
    """The Admin who made the request, as the importer wants them. The role is not checked here:
    the route did that before it accepted the scan."""
    return CurrentUser(
        user_id=inp.user_id,
        keycloak_sub=inp.keycloak_sub,
        tenant_id=inp.tenant_id,
        roles=frozenset({ROLE_ADMIN}),
    )


def _result(inp: MastersScanInput, after: dict[str, Any], failed: bool) -> MastersScanResult:
    if failed:
        return MastersScanResult(
            scan_id=inp.scan_id, state=MastersScanState.FAILED, code=ErrorCode(after["code"])
        )
    document_id = after.get("document_id")
    return MastersScanResult(
        scan_id=inp.scan_id,
        state=MastersScanState.DONE,
        document_id=uuid.UUID(document_id) if document_id else None,
        parse_pending=bool(after.get("parse_pending")),
    )


def _existing(session: Session, inp: MastersScanInput) -> MastersScanResult | None:
    for row in reversed(entity_history(session, inp.tenant_id, ENTITY, str(inp.scan_id))):
        if row.action in OUTCOMES:
            return _result(inp, row.after or {}, row.action == FAILED)
    return None


def _outcome(
    session: Session, inp: MastersScanInput, action: str, after: dict[str, Any]
) -> MastersScanResult:
    """Store the outcome (once per scan) and return it."""
    try:
        record(
            session,
            tenant_id=inp.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action=action,
            entity_type=ENTITY,
            entity_id=str(inp.scan_id),
            after=after,
            correlation_id=inp.correlation_id,
            idempotency_key=OUTCOME_KEY,
        )
        session.commit()
    except IntegrityError:  # an earlier attempt stored it first: that one stands
        session.rollback()
        found = _existing(session, inp)
        if found is not None:
            return found
        raise
    return _result(inp, after, action == FAILED)


def _failure(session: Session, inp: MastersScanInput, error: ApiError) -> MastersScanResult:
    after: dict[str, Any] = {
        "status": error.status,
        "code": error.code.value,
        "title": error.title,
        "detail": error.detail,
        "errors": [e.model_dump(mode="json") for e in error.errors],
    }
    return _outcome(session, inp, FAILED, after)


def _forget(deps: ScanDeps, inp: MastersScanInput) -> None:
    deps.storage.delete(inp.pending_key)  # a missing key is fine


def _report_scope(inp: MastersScanInput) -> StorageScope:
    return StorageScope(inp.tenant_id, inp.masters_project_id or TENANT_SCOPE_PROJECT, inp.scan_id)


def scan_and_import(deps: ScanDeps, inp: MastersScanInput) -> MastersScanResult:
    with deps.sessions() as session:
        found = _existing(session, inp)
    if found is not None:  # an activity retry after the outcome was stored
        _forget(deps, inp)
        return found
    correlation_id_var.set(inp.correlation_id)  # the audit rows the importer writes carry it
    try:
        data = deps.storage.get(inp.pending_key)
    except FileNotFoundError:
        raise ApplicationError(
            "the stored upload is gone", type=NOT_FOUND, non_retryable=True
        ) from None
    user = _user(inp)
    with deps.sessions() as session:
        try:
            if inp.kind is MastersScanKind.IMPORT:
                after = _import(deps, session, user, inp, data)
            else:
                after = _attach(deps, session, user, inp, data)
        except ApiError as exc:
            session.rollback()
            if (
                exc.status == 503
            ):  # not the file's fault: Temporal retries, then the workflow gives up
                raise ApplicationError(exc.code.value, type=UNAVAILABLE) from None
            result = _failure(session, inp, exc)
        else:
            result = _outcome(session, inp, DONE, after)
    _forget(deps, inp)
    return result


def _import(
    deps: ScanDeps, session: Session, user: CurrentUser, inp: MastersScanInput, data: bytes
) -> dict[str, Any]:
    if inp.mode is None:
        raise ApiError(422, ErrorCode.VALIDATION_ERROR, "The import has no mode")
    report = importer.import_workbook(
        session,
        user,
        deps.clamd,
        deps.storage,
        inp.filename,
        data,
        inp.mode,
        inp.masters_project_id,
    )
    stored = deps.storage.put(
        json.dumps(report.model_dump(mode="json")).encode(),
        _report_scope(inp),
        Kind.DERIVED,
        "application/json",
    )
    return {"report_key": stored.storage_key, "sha256": report.sha256}


def _attach(
    deps: ScanDeps, session: Session, user: CurrentUser, inp: MastersScanInput, data: bytes
) -> dict[str, Any]:
    done = documents.attach(
        session,
        user,
        deps.clamd,
        deps.storage,
        inp.filename,
        data,
        inp.masters_project_id,
        inp.master_type,
    )
    row = done.document
    return {
        "document_id": str(row.id),
        "created": done.created,
        "parse_pending": (
            row.scan_status == ScanStatus.CLEAN.value
            and row.parse_status == ParseStatus.PENDING.value
        ),
    }


def fail_for(deps: ScanDeps, inp: MastersScanInput) -> MastersScanResult:
    """The workflow gives up (the retries are used up): the scan is FAILED, as the synchronous
    route would have answered, and the stored bytes go."""
    error = documents.scan_unavailable()
    with deps.sessions() as session:
        found = _existing(session, inp)
        result = found if found is not None else _failure(session, inp, error)
    _forget(deps, inp)
    return result


@lru_cache(maxsize=1)
def _deps() -> ScanDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    clamd = ClamdAt(settings.clamav_host, settings.clamav_port, settings.clamav_timeout_seconds)
    return ScanDeps(sessions=mirror.sessions(), storage=make_backend(settings), clamd=clamd)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="scan_and_import_masters")
def scan_and_import_masters(inp: MastersScanInput) -> MastersScanResult:
    return scan_and_import(_deps(), inp)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="fail_masters_scan")
def fail_masters_scan(inp: MastersScanInput) -> MastersScanResult:
    return fail_for(_deps(), inp)
