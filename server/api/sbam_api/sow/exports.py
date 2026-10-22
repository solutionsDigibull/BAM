"""GET P/sow/versions/{v}/export?format= (E6.4): the version as pdf, docx or md, via a signed URL.

The handler never renders. A file already rendered for this version, status, format and template
is redirected to at once. Otherwise the `SowExport` workflow (`sow-export-<version>-<format>`;
a second request joins the running one) renders it in a worker, and the handler awaits its result
asynchronously for up to 60 s: then 302, or 503 EXPORT_TIMEOUT with `Retry-After` (the render
carries on and the retry is served from the cache).

422 for another format (FastAPI, `errors[].path=format`), 404 for an unknown version or another
tenant's / an unassigned project, 409 CONFLICT for a GENERATION_FAILED version, 409 HASH_MISMATCH if
the stored content no longer matches its sha256.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

from sbam_contracts.api import ErrorCode
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sow_api import (
    EXPORT_TEMPLATE,
    SOW_EXPORT,
    ExportFormat,
    SowExportInput,
    SowExportResult,
    sow_export_workflow_id,
)
from sbam_contracts.state_machines import SowVersionState
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError

from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.pipeline.service import TemporalUnavailable
from sbam_api.sow.repository import SowExportRepository
from sbam_api.sow.service import canonical_sha256
from sbam_api.sow.versions import _version
from sbam_api.temporal.client import OrchestratorUnavailable

EXPORT_WAIT_SECONDS: Final = 60.0
RETRY_AFTER_SECONDS: Final = 30


def export_timeout() -> ApiError:
    return ApiError(
        503,
        ErrorCode.EXPORT_TIMEOUT,
        "The export is still being rendered",
        "Large SOWs take longer; try again shortly.",
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def _check(
    session: Session, user: CurrentUser, project_id: uuid.UUID, number: int, fmt: ExportFormat
) -> tuple[uuid.UUID, str | None]:
    """The version id and, when it is cached, the file's storage key."""
    row = _version(session, user, project_id, number)
    if row.state == SowVersionState.GENERATION_FAILED.value or row.structured is None:
        raise ApiError(
            409, ErrorCode.CONFLICT, "Nothing to export", "This version's generation failed."
        )
    if canonical_sha256(row.structured) != row.sha256:
        raise ApiError(409, ErrorCode.HASH_MISMATCH, "The SOW failed its integrity check")
    hit = SowExportRepository(session, user.context(project_id)).find(
        row.id, row.state, fmt.value, EXPORT_TEMPLATE
    )
    return row.id, hit.storage_key if hit else None


async def export_key(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    number: int,
    fmt: ExportFormat,
    *,
    wait_seconds: float | None = None,
) -> str:
    """The storage key of the rendered file (rendering it first if needed)."""

    def check() -> tuple[uuid.UUID, str | None]:
        with sessions() as session:
            return _check(session, user, project_id, number, fmt)

    version_id, key = await asyncio.to_thread(check)
    if key is not None:
        return key
    inp = SowExportInput(
        tenant_id=user.context(project_id).tenant_id,
        project_id=project_id,
        run_id=uuid7(),
        correlation_id=correlation_id_var.get() or str(uuid7()),
        sow_version_id=version_id,
        format=fmt,
    )
    try:
        client = await get_client()
        handle = await client.start_workflow(
            SOW_EXPORT,
            inp,
            id=sow_export_workflow_id(version_id, fmt),
            task_queue=TaskQueue.BUILD.value,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            result_type=SowExportResult,
        )
    except (OrchestratorUnavailable, RPCError):
        raise TemporalUnavailable() from None
    try:
        wait = EXPORT_WAIT_SECONDS if wait_seconds is None else wait_seconds
        result: SowExportResult = await asyncio.wait_for(handle.result(), wait)
    except TimeoutError:
        raise export_timeout() from None
    except WorkflowFailureError:
        raise export_timeout() from None
    return result.storage_key
