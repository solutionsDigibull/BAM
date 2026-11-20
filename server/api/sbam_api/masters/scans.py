"""Masters scan and import as a run (#609, N-02b option (a)): the request side.

`POST /admin/masters/import` and `POST /admin/master-documents` answer 202 `MastersImportAccepted`
for a client that sends `Prefer: respond-async` and an `Idempotency-Key`. The request keeps the
cheap checks (role, tenant, project, size, type) and makes no clamd call: it stores the bytes
unscanned under the scan's own storage scope (`<tenant>/<project>/<scan_id>/uploads/<sha256>`, so
nothing else can reference or serve them), writes an audit row `MASTERS_SCAN_ACCEPTED` and starts
`MastersScanImport` (`sbam_workers.ingest.masters_scan`), which scans, parses and writes with the
same code the synchronous route uses.

The scan's whole state lives in tables that already exist:
  * `scan_id` = uuid5(tenant, Idempotency-Key): the same key finds the same scan and run (a key used
    for another file is 422 IDEMPOTENCY_KEY_REUSED); another tenant's `scan_id` is simply unknown;
  * the audit trail of entity `masters_scan` / `scan_id`: `MASTERS_SCAN_ACCEPTED`, then exactly one
    of `MASTERS_SCAN_DONE` (the result) or `MASTERS_SCAN_FAILED` (the problem), or
    `MASTERS_SCAN_ABANDONED` when Temporal refused the start (the stored bytes are removed, a
    repeat of the key starts afresh);
  * the run mirror (`temporal_runs`) for QUEUED / RUNNING;
  * the `ImportReport` of an IMPORT, as a JSON file in `derived/` through StorageBackend.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from sbam_contracts.api import ErrorCode, FieldError, ProblemDetail
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_api import ImportMode, ImportReport
from sbam_contracts.masters_scan import (
    MASTERS_SCAN_IMPORT,
    PREFER_ASYNC,
    MastersImportAccepted,
    MastersScanInput,
    MastersScanKind,
    MastersScanState,
    MastersScanStatus,
    masters_scan_workflow_id,
)
from sbam_contracts.masters_workbook import MAX_WORKBOOK_BYTES
from sbam_contracts.runs import RunState, SubjectType, TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import entity_history, record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.masters import documents
from sbam_api.masters.service import _active_project, _correlation
from sbam_api.storage import Kind, StorageBackend, StorageScope
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.temporal.service import StartSpec, start_run

ENTITY: Final = "masters_scan"
ACCEPTED: Final = "MASTERS_SCAN_ACCEPTED"
DONE: Final = "MASTERS_SCAN_DONE"
FAILED: Final = "MASTERS_SCAN_FAILED"
ABANDONED: Final = "MASTERS_SCAN_ABANDONED"
OUTCOMES: Final = (DONE, FAILED)
QUEUED_STAGE: Final = "queued"
XLSX: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_NAMESPACE: Final = uuid.UUID("5b0f4f0e-2d0a-5c0e-9a64-3f6d6b1e6a09")
TENANT_SCOPE_PROJECT: Final = uuid.UUID(int=0)


def wants_async(prefer: str | None) -> bool:
    """RFC 7240: `Prefer: respond-async` (other preferences in the same header are ignored)."""
    if not prefer:
        return False
    return any(p.split(";")[0].strip().lower() == PREFER_ASYNC for p in prefer.split(","))


def scan_id_for(tenant_id: uuid.UUID, key: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{tenant_id}:{key}")


@dataclass(frozen=True)
class Upload:
    kind: MastersScanKind
    filename: str
    data: bytes
    project_id: uuid.UUID | None
    mode: ImportMode | None = None
    master_type: MasterType | None = None


def _precheck(session: Session, user: CurrentUser, upload: Upload) -> None:
    """The refusals the synchronous route gives before it scans: the same codes, now at once."""
    if upload.project_id is not None:
        _active_project(session, user, upload.project_id)
    if upload.kind is MastersScanKind.DOCUMENT:
        documents.precheck(upload.filename, upload.data)
    elif len(upload.data) > MAX_WORKBOOK_BYTES:
        raise ApiError(413, ErrorCode.FILE_TOO_LARGE, "The workbook is larger than 15 MB")


def _history(session: Session, tenant_id: uuid.UUID, scan_id: uuid.UUID) -> list[AuditLog]:
    return entity_history(session, tenant_id, ENTITY, str(scan_id))


def _live(rows: list[AuditLog]) -> AuditLog | None:
    """The accepted row of the scan, unless the last start was abandoned (then: a fresh scan)."""
    if not rows or rows[-1].action == ABANDONED:
        return None
    return next((r for r in rows if r.action == ACCEPTED), None)


def _store(
    session: Session,
    user: CurrentUser,
    storage: StorageBackend,
    scan_id: uuid.UUID,
    upload: Upload,
    sha256: str,
) -> MastersScanInput:
    """Write the bytes and the accepted row (committed)."""
    tenant_id = user.context().tenant_id
    project_id = upload.project_id or TENANT_SCOPE_PROJECT
    scope = StorageScope(tenant_id, project_id, scan_id)
    mime = XLSX if upload.kind is MastersScanKind.IMPORT else "application/octet-stream"
    stored = storage.put(upload.data, scope, Kind.UPLOADS, mime)
    correlation_id = _correlation()
    inp = MastersScanInput(
        tenant_id=tenant_id,
        project_id=project_id,
        run_id=uuid7(),
        correlation_id=correlation_id if correlation_id != "unknown" else str(uuid7()),
        scan_id=scan_id,
        kind=upload.kind,
        pending_key=stored.storage_key,
        sha256=sha256,
        filename=upload.filename[:255],
        masters_project_id=upload.project_id,
        mode=upload.mode,
        master_type=upload.master_type,
        user_id=user.user_id,
        keycloak_sub=user.keycloak_sub,
    )
    record(
        session,
        tenant_id=tenant_id,
        actor_id=str(user.user_id),
        action=ACCEPTED,
        entity_type=ENTITY,
        entity_id=str(scan_id),
        after=inp.model_dump(mode="json", exclude={"run_id", "tenant_id", "user_id"}),
        correlation_id=inp.correlation_id,
    )
    session.commit()
    return inp


def _input_of(row: AuditLog, tenant_id: uuid.UUID, user: CurrentUser) -> MastersScanInput:
    """The input of an accepted scan, read back from its audit row (a repeat of the key)."""
    after = dict(row.after or {})
    return MastersScanInput.model_validate(
        {**after, "tenant_id": tenant_id, "run_id": uuid7(), "user_id": user.user_id}
    )


def _abandon(
    session: Session, user: CurrentUser, storage: StorageBackend, inp: MastersScanInput
) -> None:
    storage.delete(inp.pending_key)
    record(
        session,
        tenant_id=inp.tenant_id,
        actor_id=str(user.user_id),
        action=ABANDONED,
        entity_type=ENTITY,
        entity_id=str(inp.scan_id),
        after={"reason": ErrorCode.ORCHESTRATOR_UNAVAILABLE.value},
        correlation_id=inp.correlation_id,
    )
    session.commit()


async def accept(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    storage: StorageBackend,
    upload: Upload,
    key: str,
) -> MastersImportAccepted:
    """The 202 path. Nothing here calls clamd. Temporal unreachable: 503, no run, no stored bytes
    (a repeat of the `Idempotency-Key` starts the scan afresh)."""

    def prepare() -> MastersScanInput:
        tenant_id = user.context().tenant_id
        scan_id = scan_id_for(tenant_id, key)
        sha256 = f"sha256:{hashlib.sha256(upload.data).hexdigest()}"
        with sessions() as session:
            _precheck(session, user, upload)
            live = _live(_history(session, tenant_id, scan_id))
            if live is None:
                return _store(session, user, storage, scan_id, upload, sha256)
            prior = _input_of(live, tenant_id, user)
            if (prior.kind, prior.sha256) != (upload.kind, sha256):
                raise ApiError(
                    422,
                    ErrorCode.IDEMPOTENCY_KEY_REUSED,
                    "Idempotency-Key already used with a different request",
                )
            return prior

    def abandon(inp: MastersScanInput) -> None:
        with sessions() as session:
            _abandon(session, user, storage, inp)

    inp = await asyncio.to_thread(prepare)
    spec = StartSpec(
        workflow=MASTERS_SCAN_IMPORT,
        workflow_id=masters_scan_workflow_id(inp.scan_id),
        queue=TaskQueue.INGEST,
        subject_type=SubjectType.PROJECT,
        subject_id=inp.project_id,
        stage=QUEUED_STAGE,
        input=inp,
    )
    try:
        client = await get_client()
        run = await start_run(client, sessions, spec)
    except OrchestratorUnavailable:
        await asyncio.to_thread(abandon, inp)
        raise
    state = (
        MastersScanState.QUEUED if run.current_stage == QUEUED_STAGE else MastersScanState.RUNNING
    )
    return MastersImportAccepted(scan_id=inp.scan_id, kind=inp.kind, state=state, run=run)


# --- the status ---------------------------------------------------------------------------------


def problem_of(after: dict[str, Any], correlation_id: str) -> ProblemDetail:
    return ProblemDetail(
        title=str(after.get("title") or "The scan failed"),
        status=int(after.get("status") or 500),
        detail=after.get("detail"),
        code=ErrorCode(after["code"]),
        errors=[FieldError.model_validate(e) for e in after.get("errors", [])],
        correlation_id=correlation_id,
    )


def status(
    session: Session, user: CurrentUser, storage: StorageBackend, scan_id: uuid.UUID
) -> MastersScanStatus:
    """404 for a scan of another tenant, or one that was never accepted."""
    tenant_id = user.context().tenant_id
    rows = _history(session, tenant_id, scan_id)
    accepted = _live(rows)
    if accepted is None:
        raise not_found()
    outcome = next((r for r in reversed(rows) if r.action in OUTCOMES), None)
    run_project = uuid.UUID(
        str((accepted.after or {}).get("project_id", TENANT_SCOPE_PROJECT))
    )  # zero: tenant-wide masters
    run_row = TemporalRunRepository(
        session, TenantContext(tenant_id, run_project)
    ).latest_for_workflow_id(masters_scan_workflow_id(scan_id))
    run = to_run(run_row) if run_row is not None else None
    out = MastersScanStatus(
        scan_id=scan_id,
        kind=MastersScanKind((accepted.after or {})["kind"]),
        state=MastersScanState.QUEUED,
        run=run,
        created_at=accepted.at,
        updated_at=(outcome or accepted).at,
    )
    if outcome is not None:
        after = outcome.after or {}
        if outcome.action == FAILED:
            out.state = MastersScanState.FAILED
            out.error = problem_of(after, outcome.correlation_id)
        else:
            out.state = MastersScanState.DONE
            _attach_result(session, user, storage, out, after)
        return out
    if run is None or run.current_stage == QUEUED_STAGE:
        return out
    if run.state in (RunState.RUNNING, RunState.COMPLETED):
        out.state = MastersScanState.RUNNING
        return out
    out.state = MastersScanState.FAILED  # the run ended with no outcome: killed or timed out
    out.error = ProblemDetail(
        title="The scan did not finish",
        status=503,
        detail="The scan was stopped before it finished. Upload the file again.",
        code=ErrorCode.SERVICE_UNAVAILABLE,
        correlation_id=accepted.correlation_id,
    )
    return out


def _attach_result(
    session: Session,
    user: CurrentUser,
    storage: StorageBackend,
    out: MastersScanStatus,
    after: dict[str, Any],
) -> None:
    if out.kind is MastersScanKind.IMPORT:
        out.import_report = ImportReport.model_validate(
            json.loads(storage.get(str(after["report_key"])))
        )
        return
    out.document = documents.to_out(documents.get(session, user, uuid.UUID(after["document_id"])))
    out.created = bool(after["created"])
