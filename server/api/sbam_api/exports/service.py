"""Export of a frozen Workflow version (#133, E14.5): the API side.

`POST W/versions/{version}/export`:
1. 404 for a Workflow outside this tenant and project (the route guard and the scoped repository);
2. 409 `NOT_FROZEN` unless `workflow_versions` has the version (a CERTIFIED Workflow is not frozen);
   an unknown version is 404;
3. the run id is derived from (tenant, Workflow, version, Idempotency-Key): the same key finds the
   run it started, and is never started twice; a new key starts `ExportVersion` (`export-<run id>`).

`GET W/versions/{version}/export`: the newest `EXPORTED` audit row of the version names the stored
zip; the caller gets a 302 to a signed URL that lives `URL_TTL` seconds and is valid only for the
key's tenant. Not built yet: 404.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Final

from sbam_contracts.api import ErrorCode
from sbam_contracts.exports import EXPORT_VERSION, ExportAccepted, ExportInput, export_workflow_id
from sbam_contracts.runs import Run, SubjectType, TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.artifacts.models import ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.audit.repository import entity_history
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.pipeline.service import TemporalUnavailable
from sbam_api.storage.signing import signed_url
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.temporal.service import StartSpec, start_run
from sbam_api.versioning.models import WorkflowVersionRow
from sbam_api.versioning.repository import WorkflowVersionRepository
from sbam_api.versioning.service import not_frozen
from sbam_api.workflows.repository import WorkflowRepository

ENTITY: Final = "workflow"
EXPORTED: Final = "EXPORTED"  # audit: {version, storage_key, sha256, size, run_id}
EXPORT_FAILED: Final = "EXPORT_HASH_MISMATCH"  # audit (the alert): {version, run_id, reason}
URL_TTL: Final = 300  # seconds
_RUNS: Final = uuid.UUID("5f0c9a54-7a8e-4f61-9d0e-6b3a1c2e1335")  # uuid5 namespace of export runs


def run_id_of(ctx: TenantContext, workflow_id: uuid.UUID, version: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_RUNS, f"{ctx.tenant_id}:{workflow_id}:{version}:{key}")


def require_uuid_key(key: str) -> str:
    try:
        return str(uuid.UUID(key))
    except ValueError:
        raise ApiError(
            400, ErrorCode.VALIDATION_ERROR, "Invalid Idempotency-Key", "It must be a UUID."
        ) from None


def frozen_version(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, version: str
) -> WorkflowVersionRow:
    """The frozen version row; 404 for another tenant's Workflow or an unknown version, 409
    `NOT_FROZEN` for the Workflow's current version that is not frozen yet."""
    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    for row in WorkflowVersionRepository(session, ctx).of_workflow(workflow_id):
        if row.version == version:
            return row
    if workflow.version == version:
        raise not_frozen(f"Version {version}", workflow.state)
    raise not_found()


def _prepare(
    sessions: sessionmaker[Session],
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    version: str,
    run_id: uuid.UUID,
) -> Run | None:
    with sessions() as session:
        frozen_version(session, ctx, workflow_id, version)
        found = TemporalRunRepository(session, ctx).latest_for_workflow_id(
            export_workflow_id(run_id)
        )
        return None if found is None else to_run(found)


async def request_export(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    version: str,
    key: str,
) -> ExportAccepted:
    ctx = user.context(project_id)
    run_id = run_id_of(ctx, workflow_id, version, require_uuid_key(key))
    run = await asyncio.to_thread(_prepare, sessions, ctx, workflow_id, version, run_id)
    if run is None:
        spec = StartSpec(
            workflow=EXPORT_VERSION,
            workflow_id=export_workflow_id(run_id),
            queue=TaskQueue.CERTIFY,
            subject_type=SubjectType.PROJECT,
            subject_id=project_id,
            stage="export",
            input=ExportInput(
                tenant_id=ctx.tenant_id,
                project_id=project_id,
                run_id=run_id,
                correlation_id=correlation_id_var.get() or str(uuid7()),
                workflow_id=workflow_id,
                version=version,
                actor_id=str(user.user_id),
            ),
        )
        try:
            run = await start_run(await get_client(), sessions, spec)
        except OrchestratorUnavailable:
            raise TemporalUnavailable() from None
    return ExportAccepted(run=run, workflow_id=workflow_id, version=version)


def download_url(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    version: str,
    secret: str | None,
) -> str:
    frozen_version(session, ctx, workflow_id, version)
    for row in reversed(entity_history(session, ctx.tenant_id, ENTITY, str(workflow_id))):
        after = row.after or {}
        if row.action != EXPORTED or after.get("version") != version:
            continue
        artifact = ArtifactRepository(session, ctx).find(str(after.get("storage_key")))
        if artifact is None or artifact.kind != ArtifactKind.EXPORT.value:
            break
        return signed_url(
            secret,
            artifact.storage_key,
            ctx,
            URL_TTL,
            filename=f"workflow-export-{version}.zip",
            content_type="application/zip",
        )
    raise not_found()
