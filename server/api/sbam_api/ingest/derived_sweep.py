"""Delete derived files nothing references (#533, the safety net of #496).

The parse and OCR steps store `doc.md` / `doc.json` under their content hash and record the keys on
the version (or master document). Anything left in a scope's `derived/` that no row points at holds
a document's text and would never be removed: a crash between the OCR commit and the delete of the
text pass's files, a delete that failed, or a file orphaned before #496 shipped. This is the daily
system job that removes them, for every tenant. It reads across tenants through the system-only
queries in `repository.py` and is never reachable from a request.

* Only objects older than `GRACE` are looked at. A parse stores its files and commits their keys
  in a moment; an identical file stored again has its modified time refreshed by the backend
  (`put`), so a file in use is always young. The reference is checked again right before each
  delete.
* Only `<tenant>/<project>/derived/` of a known scope is listed; `uploads/`, `okf/` and every other
  kind are never listed, and a stray file that is not a valid key is never returned by the backend.
* A delete that fails is counted and does not stop the others. The run is bounded by `MAX_DELETES`
  and idempotent: a second run deletes nothing more.
* One `audit_log` row per run that changed or failed something (counts only: no keys, no names).
"""

import logging
from datetime import datetime, timedelta
from typing import Final

from pydantic import BaseModel
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.ids import uuid7
from sbam_api.ingest.repository import (
    derived_key_referenced,
    derived_scopes,
    referenced_derived_keys,
)
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import Kind, ListableStorage, StorageUnavailable

GRACE: Final = timedelta(hours=24)
MAX_DELETES: Final = 5_000  # per run; what is left is picked up by the next run
log = logging.getLogger("sbam_api.ingest.derived_sweep")


class SweepResult(BaseModel):
    scopes: int = 0
    listed: int = 0
    deleted: int = 0
    young: int = 0  # unreferenced but inside the grace period
    failed: int = 0


def sweep_derived_files(
    session: Session, storage: ListableStorage, as_of: datetime, correlation_id: str
) -> SweepResult:
    result = SweepResult()
    for tenant_id, project_id in derived_scopes(session):
        if result.deleted >= MAX_DELETES:
            break
        result.scopes += 1
        prefix = f"{tenant_id}/{project_id}/{Kind.DERIVED.value}/"
        referenced = referenced_derived_keys(session, tenant_id, project_id)
        try:
            for stored in storage.list_keys(prefix):
                result.listed += 1
                if stored.storage_key in referenced:
                    continue
                if as_of - stored.modified_at < GRACE:
                    result.young += 1
                    continue
                # Fresh look: a parse may have claimed the file since the list above was read.
                if derived_key_referenced(session, tenant_id, project_id, stored.storage_key):
                    continue
                try:
                    storage.delete(stored.storage_key)
                    result.deleted += 1
                except StorageUnavailable:
                    result.failed += 1
                if result.deleted >= MAX_DELETES:
                    break
        except StorageUnavailable:
            result.failed += 1
    if result.deleted or result.failed:
        record(
            session,
            tenant_id=None,  # a system run covers every tenant
            actor_id=SYSTEM_ACTOR_ID,
            action="DERIVED_FILES_SWEPT",
            entity_type="derived_sweep",
            entity_id=str(uuid7()),
            after=result.model_dump(),
            correlation_id=correlation_id,
        )
        session.commit()
    log.info("derived files swept: %s", result.model_dump())
    return result
