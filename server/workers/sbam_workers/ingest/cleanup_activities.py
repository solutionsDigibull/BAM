"""Activity of `CleanupAbandonedUploads` (E4.12): remove the leftovers of unfinished uploads.

The work itself is `sbam_api.ingest.cleanup.cleanup_abandoned_uploads` (staged files, old
reservation rows, an orphan report, one audit row). The staging directory is
`<STORAGE_ROOT>/uploads`, on the volume only the ingest worker mounts; `configure()` replaces it in
tests.
"""

import logging
from datetime import datetime
from pathlib import Path

from sbam_api.ingest.cleanup import cleanup_abandoned_uploads
from sbam_api.ingest.rules import STAGING
from sbam_contracts.documents import CleanupResult
from sbam_contracts.runs import TaskQueue
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.cleanup")

_staging_dir: Path | None = None


def configure(staging_dir: Path | None = None) -> None:
    """Use this staging directory (tests). Otherwise `<STORAGE_ROOT>/uploads` from the settings."""
    global _staging_dir
    _staging_dir = staging_dir


def _staging() -> Path:
    if _staging_dir is not None:
        return _staging_dir
    root = WorkerSettings().storage_root  # type: ignore[call-arg]
    if root is None:  # retrying cannot fix a missing setting
        raise ApplicationError(
            "STORAGE_ROOT is not set", type="StorageNotConfigured", non_retryable=True
        )
    return root / STAGING


@activity_for(TaskQueue.INGEST)
@activity.defn(name="cleanup_abandoned_uploads")
def run_cleanup(as_of: datetime) -> CleanupResult:
    """`as_of` is the workflow clock (workflow.now()), so a retry judges the same moment."""
    correlation = f"cleanup-{activity.info().workflow_run_id}"
    with mirror.sessions()() as session:
        result = cleanup_abandoned_uploads(session, _staging(), as_of, correlation)
    log.info("abandoned uploads cleaned: %s", result.model_dump())
    return result
