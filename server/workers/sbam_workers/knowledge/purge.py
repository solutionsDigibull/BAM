"""`remove_document_knowledge` (#449): the purge's knowledge step, on the knowledge queue.

`PurgeDeleted` runs on the ingest worker, which has no Cognee; before `purge_document` deletes a
document's files it runs this activity on worker-knowledge. It re-checks that the document is still
deleted and due (`retention.due_versions`) and removes the Cognee items of its DELETED versions
(item key `<version id>_<sha256>`) with the graph nodes only they sourced. Idempotent: a retry finds
nothing left. Cognee or Bifrost down is `CogneeUnavailable`: Temporal retries it, and when it still
fails the document is not purged (it stays DELETED and the next daily run tries again).
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from functools import lru_cache

from sbam_api.ingest import retention
from sbam_contracts.documents import PurgeRequest
from sbam_contracts.knowledge import ProjectScope
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter, CogneeUnavailable, ScopeError
from sbam_workers.registry import activity_for

log = logging.getLogger("sbam_workers.knowledge.purge")

STAGE = "remove_document_knowledge"

_adapter: Callable[[], CogneeAdapter] | None = None


def configure(adapter: Callable[[], CogneeAdapter]) -> None:
    """Use this adapter (tests); the real one is built from the worker settings on first use."""
    global _adapter
    _adapter = adapter


@lru_cache(maxsize=1)
def _default_adapter() -> CogneeAdapter:
    from sbam_workers.knowledge.cognee_backend import default_adapter

    return default_adapter()


def _due(sessions: sessionmaker[Session], req: PurgeRequest) -> list[uuid.UUID] | None:
    with sessions() as session:
        return retention.due_versions(session, req.target, req.as_of)


async def remove_knowledge(
    adapter: CogneeAdapter, sessions: sessionmaker[Session], req: PurgeRequest
) -> int:
    """How many items were removed; see the module docstring."""
    versions = await asyncio.to_thread(_due, sessions, req)
    if not versions:
        return 0
    scope = ProjectScope(tenant_id=req.target.tenant_id, project_id=req.target.project_id)
    try:
        return len(await adapter.remove_sources(scope, set(versions)))
    except CogneeUnavailable as exc:
        raise ApplicationError(str(exc), type="CogneeUnavailable") from None  # retryable
    except ScopeError:  # no such project in the tenant: it has no dataset to clean
        log.warning(
            "purge: project not in tenant, no knowledge to remove",
            extra={"tenant_id": str(scope.tenant_id), "project_id": str(scope.project_id)},
        )
        return 0


@activity_for(TaskQueue.KNOWLEDGE)
@activity.defn(name=STAGE)
async def remove_document_knowledge(req: PurgeRequest) -> int:
    adapter = (_adapter or _default_adapter)()
    return await remove_knowledge(adapter, mirror.sessions(), req)
