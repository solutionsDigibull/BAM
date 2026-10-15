"""Clean up abandoned uploads (E4.12, TD §5.2).

An upload that never finishes leaves two things behind: its reservation row, and, once it was
created in tusd, the staged file `<STORAGE_ROOT>/uploads/<reservation id>` with a `.info` file next
to it (the reservation id is the tus upload id, so the file name is a UUID we chose). This is the
daily job that removes both, for every tenant. It is a system job: it reads across tenants through
the system-only queries in `repository.py` and is never reachable from a request.

* A reservation that is still `RESERVED` or `CREATED` after its hold (`expires_at`) is abandoned.
  Its staged file is removed. There is no `EXPIRED` status: a row whose hold has ended is already
  ignored by the 20-file count, so the end of the hold is the mark.
* Such a row is deleted once its hold ended more than `ABANDONED_ROW_RETENTION` ago.
* A staged file that no reservation owns, and that is older than the hold plus `ORPHAN_GRACE`, is
  an orphan. It is counted and logged, never deleted: it is not ours to guess about.
* `FINISHED` and `DUPLICATE` reservations, and every document and version, are never touched.
* One `audit_log` row per run says what it did (counts only: no names, no content).

The staged files are tusd's temporary files, not stored objects, so they are removed with plain file
calls rather than `StorageBackend` (the same exception the post-finish hook makes). The job is
idempotent, and a file that cannot be removed is counted and does not stop the others.
"""

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from sbam_contracts.documents import CleanupResult
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.ids import uuid7
from sbam_api.ingest.repository import (
    abandoned_reservations,
    delete_reservations,
    known_reservation_ids,
)
from sbam_api.ingest.rules import ABANDONED_ROW_RETENTION, ORPHAN_GRACE, UPLOAD_HOLD
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID

log = logging.getLogger("sbam_api.ingest.cleanup")

CHUNK = 500  # reservations per round trip
MAX_ROWS = 20_000  # per run; what is left is picked up by the next run
_STAGED = re.compile(
    r"^(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(\.info)?$"
)


def cleanup_abandoned_uploads(
    session: Session, staging_dir: Path, as_of: datetime, correlation_id: str
) -> CleanupResult:
    result = CleanupResult()
    _reservations(session, staging_dir, as_of, result)
    result.orphans = _orphans(session, staging_dir, as_of)
    if result.files_removed or result.rows_deleted or result.orphans or result.failed:
        record(
            session,
            tenant_id=None,  # a system run covers every tenant
            actor_id=SYSTEM_ACTOR_ID,
            action="UPLOADS_CLEANED",
            entity_type="upload_cleanup",
            entity_id=str(uuid7()),
            after=result.model_dump(),
            correlation_id=correlation_id,
        )
        session.commit()
    return result


def _reservations(
    session: Session, staging_dir: Path, as_of: datetime, result: CleanupResult
) -> None:
    """Two passes, so a bounded run can never starve the rows it did not reach.

    1. Rows past their retention: their files go and the row is deleted, oldest first. A deleted
       row leaves the queue, so repeated runs always make progress.
    2. Rows still inside their retention: their files go, newest first. These rows stay, so the same
       ones are looked at again on the next days (a cheap no-op); newest first means a fresh
       abandonment is cleaned by the first run after its hold ended. Only more than `MAX_ROWS`
       abandoned uploads inside one retention window can leave the oldest of them to pass 1.
    """
    delete_before = as_of - ABANDONED_ROW_RETENTION
    _pass(session, staging_dir, result, ended_by=delete_before, newest_first=False, delete=True)
    _pass(
        session,
        staging_dir,
        result,
        ended_by=as_of,
        ended_after=delete_before,
        newest_first=True,
        delete=False,
    )


def _pass(
    session: Session,
    staging_dir: Path,
    result: CleanupResult,
    *,
    ended_by: datetime,
    newest_first: bool,
    delete: bool,
    ended_after: datetime | None = None,
) -> None:
    after: uuid.UUID | None = None
    seen = 0
    while seen < MAX_ROWS:
        rows = abandoned_reservations(
            session,
            ended_by=ended_by,
            ended_after=ended_after,
            after=after,
            newest_first=newest_first,
            limit=CHUNK,
        )
        if not rows:
            return
        seen += len(rows)
        after = rows[-1].id
        deletable: list[uuid.UUID] = []
        for row in rows:
            removed, ok = _remove_staged(staging_dir, row.id)
            result.files_removed += removed
            if not ok:
                result.failed += 1
            elif delete:
                deletable.append(row.id)  # its file is gone and its retention is over
        if deletable:
            result.rows_deleted += delete_reservations(session, deletable)
        session.commit()


def _remove_staged(staging_dir: Path, reservation_id: uuid.UUID) -> tuple[int, bool]:
    """Remove `<id>` and `<id>.info`. Returns (files removed, no file refused to go). The name is a
    UUID from our own table, so it can never point outside the staging directory."""
    removed, ok = 0, True
    for name in (str(reservation_id), f"{reservation_id}.info"):
        path = staging_dir / name
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            ok = False
            log.warning("could not remove a staged upload", extra={"exc_type": type(exc).__name__})
    return removed, ok


def _orphans(session: Session, staging_dir: Path, as_of: datetime) -> int:
    """Staged files nobody owns. Counted and logged, never deleted."""
    if not staging_dir.is_dir():
        return 0
    cutoff = (as_of - UPLOAD_HOLD - ORPHAN_GRACE).timestamp()
    candidates: dict[uuid.UUID, list[str]] = {}
    for entry in staging_dir.iterdir():
        match = _STAGED.match(entry.name)
        if match is None:
            continue  # not one of ours (for example tusd's own lock files)
        try:
            if not entry.is_file() or entry.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        candidates.setdefault(uuid.UUID(match["id"]), []).append(entry.name)
    owned = known_reservation_ids(session, list(candidates))
    orphans = {uid: names for uid, names in candidates.items() if uid not in owned}
    if orphans:
        log.warning(
            "staged uploads with no reservation: %d file(s), for example %s",
            sum(len(names) for names in orphans.values()),
            ", ".join(sorted(str(uid) for uid in orphans)[:3]),
        )
    return sum(len(names) for names in orphans.values())
