"""The scan (E4.3, TD §4 T3): `scan_clamav` and `mark_scan_failed` activities.

`scan_version` is the whole step and takes its dependencies as arguments, so tests run it with a
fake clamd and a temp-dir storage. The activities only build the real dependencies.

Order, for a version in state UPLOADED (a retry finds it in SCANNING and carries on):
  1. `scan_started` (UPLOADED -> SCANNING), idempotent per run;
  2. clamd VERSION: refuse when the signatures are older than the limit (retryable);
  3. stream the stored file to clamd (INSTREAM); no verdict means an error, never a pass;
  4. CLEAN: scan columns + `scan_clean` (-> PARSING) in one transaction;
     INFECTED: copy to `quarantine/<sha256>`, scan columns + `malware_detected` (-> REJECTED,
     code MALWARE) in one transaction, then remove the original from `uploads/`.
After the last failed attempt the workflow calls `mark_scan_failed` (-> SCAN_FAILED): never a
silent pass.
Nothing here logs file names or content.
"""

import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

import sbam_api.ingest.models  # noqa: F401 - registers DocumentVersion for the state machine
from sbam_api.audit.outbox import LogSink, dispatch_pending
from sbam_api.audit.repository import record
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import DocumentVersion
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import MAX_FILE_BYTES, ScanOutcome, ScanStatus
from sbam_contracts.pipeline import StageRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import Actor
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.ingest import metrics
from sbam_workers.ingest.clamd import (
    ClamdClient,
    ClamVersion,
    require_fresh,
    signature_age,
    stale_reason,
)
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.scan")

NOT_FOUND = "NotFound"  # the version or its file is gone: retrying cannot help
NOT_SCANNABLE = "NotScannable"  # the version is in a state that cannot be scanned
PERMANENT = frozenset({NOT_FOUND, NOT_SCANNABLE})


@dataclass(frozen=True)
class ScanDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend
    clamd: ClamdClient
    max_signature_age: timedelta
    clock: Callable[[], datetime] = utcnow


def scan_version(deps: ScanDeps, inp: StageRef) -> ScanOutcome:
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        if version.scan_status in (ScanStatus.CLEAN.value, ScanStatus.INFECTED.value):
            if version.scan_status == ScanStatus.INFECTED.value:
                _remove_original(deps, ctx, version)  # a retry after the move: finish the cleanup
            return _outcome(version)  # already scanned (an activity retry): same answer
        if version.state == S.UPLOADED.value:
            _transition(session, ctx, inp, version.id, "scan_started")
            session.commit()
        elif version.state != S.SCANNING.value:
            raise ApplicationError(
                f"cannot scan a version in state {version.state}",
                type=NOT_SCANNABLE,
                non_retryable=True,
            )
        key, sha256, mime = version.storage_key, version.sha256, version.mime

    signature, now = _fresh_signatures(deps)
    require_fresh(signature, now, deps.max_signature_age)  # stale: retryable, nothing is read
    started = time.monotonic()
    try:
        with deps.storage.open(key) as stream:
            verdict = deps.clamd.scan(stream)
    except FileNotFoundError:
        raise ApplicationError(
            "stored file is missing", type=NOT_FOUND, non_retryable=True
        ) from None
    metrics.observe_scan(time.monotonic() - started, infected=verdict.infected)

    scope = StorageScope(inp.tenant_id, inp.project_id)
    quarantine_key: str | None = None
    if verdict.infected:
        with deps.storage.open(key) as stream:
            stored = deps.storage.put(
                stream, scope, Kind.QUARANTINE, mime, max_bytes=MAX_FILE_BYTES
            )
        if stored.sha256 != sha256:
            raise RuntimeError("quarantine copy does not match the upload")  # retried, never passes
        quarantine_key = stored.storage_key

    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        _transition(
            session, ctx, inp, version.id, "malware_detected" if verdict.infected else "scan_clean"
        )
        version = _load(session, ctx, inp.ref_id)
        version.scan_signature_version = signature.raw[:100]
        version.scanned_at = deps.clock()
        if verdict.infected and quarantine_key is not None:
            version.scan_status = ScanStatus.INFECTED.value
            version.reject_code = ErrorCode.MALWARE.value
            record(
                session,
                tenant_id=inp.tenant_id,
                actor_id=SYSTEM_ACTOR_ID,
                action="DOCUMENT_QUARANTINED",
                entity_type="document_version",
                entity_id=str(version.id),
                after={"signature": verdict.signature, "quarantine_key": quarantine_key},
                correlation_id=inp.correlation_id,
            )
            original = version.storage_key
            version.storage_key = quarantine_key
        else:
            version.scan_status = ScanStatus.CLEAN.value
            original = key
        session.commit()
        outcome = _outcome(version)
    _dispatch(deps.sessions)
    if verdict.infected:
        _remove_original(deps, ctx, version, original_key=original)
    return outcome


RELOAD_EVERY = 300.0  # seconds: how often a stale database may make us ask clamd to reload
RELOAD_WAIT = 30.0  # seconds to wait for clamd to load the new database
_last_reload = float("-inf")


def _fresh_signatures(deps: ScanDeps) -> tuple[ClamVersion, datetime]:
    """clamd's VERSION, after one reload if it looks stale.

    A new clamd serves the database baked into its image until it notices what freshclam has
    already downloaded (up to 10 minutes), so the first scans of a fresh environment would be
    refused for no good reason. Asking it to reload fixes that; the reload is throttled so a
    database that is stale upstream does not make every scan reload it.
    """
    global _last_reload
    signature, now = deps.clamd.version(), deps.clock()
    metrics.observe_signature_age(signature, now)
    if (
        stale_reason(signature, now, deps.max_signature_age)
        and time.monotonic() - _last_reload > RELOAD_EVERY
    ):
        _last_reload = time.monotonic()
        signature, now = deps.clamd.refresh_signatures(signature, wait=RELOAD_WAIT), deps.clock()
        metrics.observe_signature_age(signature, now)
    return signature, now


def mark_failed(deps: ScanDeps, inp: StageRef) -> ScanOutcome:
    """The last attempt failed: SCAN_FAILED, so the FDE sees it and can retry. Never regresses a
    version that was scanned in the meantime."""
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        if version.scan_status in (ScanStatus.CLEAN.value, ScanStatus.INFECTED.value):
            return _outcome(version)
        if version.state not in (S.UPLOADED.value, S.SCANNING.value):
            raise ApplicationError(
                f"cannot mark a version in state {version.state} as unscanned",
                type=NOT_SCANNABLE,
                non_retryable=True,
            )
        if version.state == S.UPLOADED.value:  # never got as far as `scan_started`
            _transition(session, ctx, inp, version.id, "scan_started")
        _transition(session, ctx, inp, version.id, "scan_unavailable")
        version = _load(session, ctx, inp.ref_id)
        version.scan_status = ScanStatus.SCAN_FAILED.value
        version.scanned_at = deps.clock()
        session.commit()
        outcome = _outcome(version)
    _dispatch(deps.sessions)
    return outcome


def _load(session: Session, ctx: TenantContext, version_id: uuid.UUID) -> DocumentVersion:
    session.expire_all()
    version: DocumentVersion | None = DocumentVersionRepository(session, ctx).get(version_id)
    if version is None:
        raise ApplicationError("document version not found", type=NOT_FOUND, non_retryable=True)
    return version


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


def _outcome(version: DocumentVersion) -> ScanOutcome:
    return ScanOutcome(
        document_version_id=version.id,
        verdict=ScanStatus(version.scan_status or ScanStatus.SCAN_FAILED.value),
        signature_version=version.scan_signature_version,
    )


def _remove_original(
    deps: ScanDeps, ctx: TenantContext, version: DocumentVersion, original_key: str | None = None
) -> None:
    """Take the infected file out of `uploads/`, unless another version still points at it."""
    key = original_key or StorageScope(ctx.tenant_id, version.project_id).key(
        Kind.UPLOADS, version.sha256
    )
    try:
        with deps.sessions() as session:
            if DocumentVersionRepository(session, ctx).others_using_key(key, version.id):
                return
        deps.storage.delete(key)
    except Exception as exc:  # the verdict is recorded; a later retry repeats this
        log.warning(
            "could not remove the original of a quarantined file", extra={"exc": type(exc).__name__}
        )


def _dispatch(sessions: sessionmaker[Session]) -> None:
    """After commit only; a failed dispatch leaves the notification pending."""
    try:
        dispatch_pending(sessions, LogSink())
    except Exception as exc:
        log.warning("notification dispatch failed", extra={"exc_type": type(exc).__name__})


@lru_cache(maxsize=1)
def _deps() -> ScanDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    metrics.start(_signature_age_probe, settings)  # no-op when the import-time start ran
    return ScanDeps(
        sessions=mirror.sessions(),
        storage=make_backend(settings),
        clamd=ClamdClient(
            settings.clamav_host, settings.clamav_port, settings.clamav_timeout_seconds
        ),
        max_signature_age=timedelta(hours=settings.clamav_max_signature_age_hours),
    )


@activity_for(TaskQueue.INGEST)
@activity.defn(name="scan_clamav")
def scan_clamav(inp: StageRef) -> ScanOutcome:
    return scan_version(_deps(), inp)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="mark_scan_failed")
def mark_scan_failed(inp: StageRef) -> ScanOutcome:
    return mark_failed(_deps(), inp)


def _signature_age_probe() -> float | None:
    deps = _deps()
    version = deps.clamd.version()
    now = deps.clock()
    age = signature_age(version, now)
    return None if age is None else age.total_seconds()


if "sbam-ingest" in os.environ.get("SBAM_QUEUES", ""):  # the other workers never reach clamd
    metrics.start(_signature_age_probe, WorkerSettings())  # type: ignore[call-arg]
