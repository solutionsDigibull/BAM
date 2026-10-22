"""Hand a registered document version to Temporal (E4.2-T10).

`DocumentIngestion` is started with the id `ingest-<document_version_id>`, so starting twice finds
the same execution. If Temporal is down the version stays UPLOADED with `ingestion_started_at`
empty and the upload still succeeds. The next upload of that project, and the start of processing
(#69), start the ones that are waiting, and the pipeline's own ingest step covers the rest.
"""

import asyncio
import logging
import uuid
from datetime import datetime

from sbam_contracts.documents import DOCUMENT_INGESTION
from sbam_contracts.pipeline import StageRef
from sbam_contracts.runs import Run, SubjectType, TaskQueue, ingest_workflow_id
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.temporal.service import StartSpec, start_run

log = logging.getLogger("sbam_api.ingest")
STAGE = "ingest"


async def start_ingestion(
    client: Client,
    sessions: sessionmaker[Session],
    ctx: TenantContext,
    version_id: uuid.UUID,
    correlation_id: str,
    *,
    with_search_attributes: bool = True,
) -> bool:
    """True when the workflow is running for this version. False = Temporal unavailable."""
    run = await start_ingestion_run(
        client, sessions, ctx, version_id, correlation_id,
        with_search_attributes=with_search_attributes,
    )  # fmt: skip
    return run is not None


async def start_ingestion_run(
    client: Client,
    sessions: sessionmaker[Session],
    ctx: TenantContext,
    version_id: uuid.UUID,
    correlation_id: str,
    *,
    with_search_attributes: bool = True,
) -> Run | None:
    """The run started (or found running) for this version; None = Temporal is unavailable."""
    if ctx.project_id is None:
        raise ValueError("ingestion needs a project")
    spec = StartSpec(
        workflow=DOCUMENT_INGESTION,
        workflow_id=ingest_workflow_id(version_id),
        queue=TaskQueue.INGEST,
        subject_type=SubjectType.DOCUMENT_VERSION,
        subject_id=version_id,
        stage=STAGE,
        input=StageRef(
            tenant_id=ctx.tenant_id,
            project_id=ctx.project_id,
            run_id=uuid7(),
            correlation_id=correlation_id,
            ref_id=version_id,
        ),
    )
    try:
        run = await start_run(client, sessions, spec, with_search_attributes=with_search_attributes)
    except ApiError as exc:  # Temporal down (503): the version waits, the upload stands
        log.warning("ingestion not started yet: %s", exc.code)
        return None
    await asyncio.to_thread(_mark_started, sessions, ctx, version_id, utcnow())
    return run


def _mark_started(
    sessions: sessionmaker[Session], ctx: TenantContext, version_id: uuid.UUID, at: datetime
) -> None:
    with sessions() as session:
        version = DocumentVersionRepository(session, ctx).get(version_id)
        if version is not None and version.ingestion_started_at is None:
            version.ingestion_started_at = at
            session.commit()


async def start_waiting(
    client: Client,
    sessions: sessionmaker[Session],
    ctx: TenantContext,
    correlation_id: str,
    *,
    with_search_attributes: bool = True,
) -> int:
    """Start every version of this project still waiting; stops at the first failure."""
    waiting = await asyncio.to_thread(_waiting_ids, sessions, ctx)
    started = 0
    for version_id in waiting:
        ok = await start_ingestion(
            client, sessions, ctx, version_id, correlation_id,
            with_search_attributes=with_search_attributes,
        )  # fmt: skip
        if not ok:
            break
        started += 1
    return started


def _waiting_ids(sessions: sessionmaker[Session], ctx: TenantContext) -> list[uuid.UUID]:
    with sessions() as session:
        return [v.id for v in DocumentVersionRepository(session, ctx).waiting_to_start()]
