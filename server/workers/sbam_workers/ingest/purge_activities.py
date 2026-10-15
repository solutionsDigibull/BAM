"""Activities of `PurgeDeleted` (E4.9): find the due documents, purge one document.

`purge_document` is one activity per document, so one failing document (storage down) never blocks
the others, and Temporal retries just that one. The work itself is
`sbam_api.ingest.retention.purge_document`: idempotent, and it re-checks under the row lock that the
document is still deleted and due, so a document restored after it was selected is left alone.

The storage backend and the cleanup of derivatives / Cognee references are module-level seams:
`configure()` in tests, built from the worker settings on first use otherwise.
"""

import threading
from datetime import datetime
from uuid import UUID

from sbam_api.ingest import retention
from sbam_api.ingest.repository import due_for_purge
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.factory import make_backend
from sbam_contracts.documents import PurgeOutcome, PurgeRequest, PurgeTarget
from sbam_contracts.runs import TaskQueue
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

LIST_LIMIT = 100  # documents per round; the workflow asks again until none are left

_lock = threading.Lock()
_storage: StorageBackend | None = None
_cleanup: retention.PurgeCleanup = retention.NoCleanup()


def configure(
    storage: StorageBackend | None = None, cleanup: retention.PurgeCleanup | None = None
) -> None:
    """Use this storage / cleanup (tests, and the knowledge module once it owns derivatives)."""
    global _storage, _cleanup
    if storage is not None:
        _storage = storage
    if cleanup is not None:
        _cleanup = cleanup


def _backend() -> StorageBackend:
    global _storage
    with _lock:
        if _storage is None:
            try:
                _storage = make_backend(WorkerSettings())  # type: ignore[call-arg]
            except ValueError as exc:  # no STORAGE_ROOT / S3_BUCKET: retrying cannot fix it
                raise ApplicationError(
                    str(exc), type="StorageNotConfigured", non_retryable=True
                ) from exc
        return _storage


@activity_for(TaskQueue.INGEST)
@activity.defn(name="list_purgeable_documents")
def list_purgeable_documents(as_of: datetime, exclude: list[str]) -> list[PurgeTarget]:
    """Soft-deleted documents of every tenant whose `purge_after` is at or before `as_of`."""
    with mirror.sessions()() as session:
        rows = due_for_purge(session, as_of, LIST_LIMIT, [UUID(x) for x in exclude])
        return [
            PurgeTarget(tenant_id=d.tenant_id, project_id=d.project_id, document_id=d.id)
            for d in rows
        ]


@activity_for(TaskQueue.INGEST)
@activity.defn(name="purge_document")
def purge_document(req: PurgeRequest) -> PurgeOutcome:
    info = activity.info()
    purge = retention.PurgeContext(
        storage=_backend(),
        cleanup=_cleanup,
        correlation_id=f"purge-{info.workflow_run_id}",
    )
    with mirror.sessions()() as session:
        return retention.purge_document(session, req.target, req.as_of, purge)
