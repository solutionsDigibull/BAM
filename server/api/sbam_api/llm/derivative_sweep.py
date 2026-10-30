"""Delete cached model answers nobody has used for a while (#497).

`llm_derivatives` holds validated answers derived from document content (SOW text, ISATVON rows,
OCR markdown), addressed by a hash of the exact messages. The hash cannot say which document an
answer came from, so a document's purge cannot find its rows; without a limit a summary of a purged
document could stay for ever. This is the daily system job that bounds it: a row whose last use
(`last_hit_at`, else `created_at`) is older than the TTL is deleted, in every tenant. Reading a row
for a hit refreshes `last_hit_at`, so an answer in use stays; one unused for the TTL (default 90
days, the document bin's retention) goes, and the next identical request is simply asked again.

* The job runs across tenants through the system-only query in `repository.py` and is never
  reachable from a request.
* Bounded by `MAX_DELETES` per run and idempotent: what is left is picked up by the next run.
* One `audit_log` row per run that deleted something (counts only: no keys, no content).
* A TTL of 0 keeps everything (a deployment that wants the cache for ever says so).
"""

import logging
from datetime import datetime, timedelta
from typing import Final

from pydantic import BaseModel
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.ids import uuid7
from sbam_api.llm.repository import delete_unused_derivatives
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID

MAX_DELETES: Final = 5_000  # per run; what is left is picked up by the next run
log = logging.getLogger("sbam_api.llm.derivative_sweep")


class SweepResult(BaseModel):
    ttl_days: int
    deleted: int = 0


def sweep_llm_derivatives(
    session: Session, as_of: datetime, ttl_days: int, correlation_id: str
) -> SweepResult:
    result = SweepResult(ttl_days=ttl_days)
    if ttl_days <= 0:
        return result
    result.deleted = delete_unused_derivatives(
        session, as_of - timedelta(days=ttl_days), MAX_DELETES
    )
    if result.deleted:
        record(
            session,
            tenant_id=None,  # a system run covers every tenant
            actor_id=SYSTEM_ACTOR_ID,
            action="LLM_DERIVATIVES_SWEPT",
            entity_type="llm_derivative_sweep",
            entity_id=str(uuid7()),
            after=result.model_dump(),
            correlation_id=correlation_id,
        )
    session.commit()
    log.info("llm derivatives swept: %s", result.model_dump())
    return result
