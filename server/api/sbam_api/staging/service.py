"""Staging, the API side (#765): the status, the bundle's files and the route to the MCP server.

Everything is scoped to the caller's tenant and project through the repositories, and the router
only admits the assigned FDE: another tenant, an unassigned FDE, a missing Workflow and a Workflow
that was never committed to Staging all answer the same 404. Nothing here starts or stops a
container: that is the runtime (`sbam_workers.staging.runtime`), run by `WorkflowCommit`.

The bundle is served only after it is checked against the digest the registry entry (and so the
frozen manifest) records: a tar that is gone or no longer matches is 409 `UI_BUNDLE_CHANGED` and
nothing is sent. The MCP route forwards to the address the runtime recorded for the running
deployment, never to an address the caller names.
"""

import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from typing import Final

from sbam_contracts.api import ErrorCode
from sbam_contracts.canonical import canonical_json
from sbam_contracts.staging import StagingHealth, StagingState, StagingStatus
from sbam_contracts.workflow_registry import RegistryKind
from sqlalchemy.orm import Session

from sbam_api.audit import repository as audit
from sbam_api.builds import workflow_ui
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.staging import records
from sbam_api.storage.base import StorageBackend
from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_api.workflow_registry.repository import RegisteredWorkflowRepository, version_key
from sbam_api.workflows.repository import WorkflowRepository

W: Final = "/projects/{project_id}/workflows/{workflow_id}"


def _entry(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> RegisteredWorkflowRow:
    """The Workflow's current STAGING entry; a Workflow of nobody, or never committed: 404."""
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    entry = RegisteredWorkflowRepository(session, ctx).current(workflow_id, RegistryKind.STAGING)
    if entry is None:
        raise not_found()
    return entry


def not_running(detail: str) -> ApiError:
    return ApiError(409, ErrorCode.CONFLICT, "Staging is not running", detail)


def status(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    health: StagingHealth | None = None,
) -> StagingStatus:
    """The current deployment and every earlier one. `health` is filled in by the caller (it needs
    a network call); None means unknown."""
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    deployed = records.replay(session, ctx.tenant_id, workflow_id)
    entry = RegisteredWorkflowRepository(session, ctx).current(workflow_id, RegistryKind.STAGING)
    history = [
        d.model()
        for d in sorted(deployed.values(), key=lambda d: version_key(d.version), reverse=True)
    ]
    if entry is None:
        return StagingStatus(
            workflow_id=workflow_id,
            state=StagingState.NOT_DEPLOYED,
            health=StagingHealth.UNKNOWN,
            history=history,
        )
    found = deployed.get(entry.version)
    if found is None:  # committed, and the runtime has recorded nothing (none configured yet)
        shown = records.Deployment(
            version=entry.version,
            registry_name=entry.registry_name,
            image_digest=entry.image_digest,
            state=StagingState.PENDING_RUNTIME,
            detail="no Staging runtime has started this version yet",
        ).model()
        if entry.ui_bundle:
            shown = shown.model_copy(update={"ui_bundle_digest": str(entry.ui_bundle["digest"])})
    else:
        shown = found.model()
    running = shown.state is StagingState.RUNNING
    prefix = "/api/v1" + W.format(project_id=ctx.project_id, workflow_id=workflow_id)
    return StagingStatus(
        workflow_id=workflow_id,
        state=shown.state,
        health=(health or StagingHealth.UNKNOWN) if running else StagingHealth.UNKNOWN,
        current=shown,
        ui_url=f"{prefix}/staging/ui/" if entry.ui_bundle else None,
        mcp_url=f"{prefix}/staging/mcp" if running else None,
        history=history,
    )


def endpoint_of_running(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> str:
    """The internal address of the CURRENT version's running container, or 404 / 409."""
    entry = _entry(session, ctx, workflow_id)
    deployed = records.replay(session, ctx.tenant_id, workflow_id).get(entry.version)
    if deployed is None or deployed.state is not StagingState.RUNNING or deployed.started is None:
        raise not_running("the Staging container of the current version is not running")
    return deployed.started.endpoint


def health_target(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> str | None:
    """The address to probe for health, or None when nothing runs (never raises 409)."""
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    try:
        return endpoint_of_running(session, ctx, workflow_id)
    except ApiError as error:
        if error.status in (404, 409):
            return None
        raise


@dataclass(frozen=True)
class Served:
    path: str
    data: bytes
    content_type: str
    allowed_hosts: list[str]


def _recorded_bundle(
    session: Session, tenant_id: uuid.UUID, workflow_id: uuid.UUID, digest: str
) -> workflow_ui.WorkflowUiRecord | None:
    """The stored bundle whose digest is the one the registry entry certified (not merely the
    newest build: a later, uncommitted build must not change what Staging serves)."""
    history = audit.entity_history(session, tenant_id, workflow_ui.ENTITY, str(workflow_id))
    for row in reversed(history):
        if row.action == workflow_ui.BUILT_ACTION and row.after is not None:
            found = workflow_ui.WorkflowUiRecord.model_validate(row.after)
            if found.digest == digest:
                return found
    return None


def bundle_files(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    storage: StorageBackend,
    entry: RegisteredWorkflowRow,
) -> dict[str, bytes]:
    """The files of the certified bundle, verified against the digest the entry records."""
    if not entry.ui_bundle:
        raise not_found()
    certified = str(entry.ui_bundle["digest"])
    record = _recorded_bundle(session, ctx.tenant_id, workflow_id, certified)
    files = None if record is None else workflow_ui.packaged_files(storage, record)
    if files is not None:
        digests = {p: hashlib.sha256(d).hexdigest() for p, d in sorted(files.items())}
        if "sha256:" + hashlib.sha256(canonical_json(digests)).hexdigest() != certified:
            files = None
    if files is None:
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "The UI bundle changed",
            "UI_BUNDLE_CHANGED: the stored bundle no longer matches the certified digest",
        )
    return files


def ui_file(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    storage: StorageBackend,
    path: str,
) -> Served:
    """One file of the certified bundle (`path` empty: its entry file). 404 for a file that is not
    in the bundle; 409 `UI_BUNDLE_CHANGED` when the stored tar no longer matches its digest."""
    entry = _entry(session, ctx, workflow_id)
    files = bundle_files(session, ctx, workflow_id, storage, entry)
    if not entry.ui_bundle:  # bundle_files raised 404 already
        raise not_found()
    wanted = path.strip("/") or str(entry.ui_bundle["entry_file"])
    data = files.get(wanted)
    if data is None:
        raise not_found()
    kind = mimetypes.guess_type(wanted)[0] or "application/octet-stream"
    return Served(wanted, data, kind, [str(h) for h in entry.ui_bundle.get("allowed_hosts", [])])


def content_security_policy(hosts: list[str]) -> str:
    """The bundle may load its own files and reach its own workflow server and the declared
    hosts."""
    remote = "".join(f" https://{h}" for h in hosts)
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'{remote}; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    )
