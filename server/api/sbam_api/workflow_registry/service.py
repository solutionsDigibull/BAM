"""Register a certified, frozen Workflow in Bifrost, supersede the older one, look one up (#760).

Rules (ADR 0028, decision 4; ADR 0013):

- Nothing uncertified is ever registered. The Workflow must be FROZEN, its manifest must be frozen
  and carry the built MCP server, and a WORKFLOW certificate must exist on that manifest's hash.
  The digest that is registered is the digest in that manifest, so SRIM's lookup returns what was
  certified.
- Registering is idempotent: the same version with the same digest is a no-op and does not call
  Bifrost at all. The same version with another digest is refused (a frozen version never changes).
- A new version supersedes the older one OF THE SAME KIND: its Bifrost entry is disabled and its
  row is marked SUPERSEDED. Nothing is deleted (no revocation, C-31). An older version cannot be
  registered over a newer one.
- Every entry has a kind (ADR 0032, #860). Commit registers `staging`: its own Bifrost entry, named
  `<production name>_stg`, that only the assigned FDE can reach. SRIM's key sees `production`
  entries only (`lookup(..., kind=PRODUCTION)`, `names_for_key`). Both kinds carry the digest that
  was certified: nothing is rebuilt.
- SBAM's table is the source of truth. `reconcile` rebuilds a lost Bifrost entry from it and
  finishes a disable that did not go through.

Where the server runs is not decided here: `address_for` turns the registry name into the address
Bifrost connects to. The default is the name on port 8000 (`workflow_server.py` serves HTTP there).
The `ctx` of every function names the tenant and the project of the Workflow.
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sbam_contracts.api import ErrorCode
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.manifest import (
    MCP_SERVER_NAME,
    McpServerEntry,
    McpToolEntry,
    UiBundleEntry,
    WorkflowManifest,
)
from sbam_contracts.state_machines import WorkflowState
from sbam_contracts.workflow_registry import RegisteredWorkflow, RegistryKind, RegistryStatus
from sqlalchemy.orm import Session

from sbam_api.certifications.models import CertificationLevel
from sbam_api.certifications.repository import CertificationRepository
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.manifests import ui_bundle
from sbam_api.manifests.repository import GateResultRepository, WorkflowManifestRepository
from sbam_api.workflow_registry.bifrost import BifrostRegistry, RegistryUnavailable
from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_api.workflow_registry.repository import RegisteredWorkflowRepository, version_key
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository

SERVER_PORT = 8000
NAME_MAX = 64
STAGING_SUFFIX = "_stg"

AddressFor = Callable[[str], str]


class NotRegistrable(ApiError):  # noqa: N818 - names from the spec
    """The Workflow is not certified and frozen, or its server does not match its manifest."""

    def __init__(self, reason: str) -> None:
        super().__init__(409, ErrorCode.CONFLICT, "Not registrable", reason)


class RegistrationConflict(ApiError):  # noqa: N818
    """This version is already registered with another digest or manifest hash."""

    def __init__(self, version: str) -> None:
        super().__init__(
            409,
            ErrorCode.CONFLICT,
            "Already registered",
            f"Version {version} is registered with a different image or manifest; "
            "a frozen version never changes, so publish a new version.",
        )


def default_address(registry_name: str) -> str:
    """The generated server listens on 8000 over HTTP; the container is named like its entry."""
    return f"http://{registry_name}:{SERVER_PORT}/mcp"


def registry_name_for(
    server_name: str, version: str, kind: RegistryKind = RegistryKind.PRODUCTION
) -> str:
    """`wf_<workflow>_v<major>_<patch>`: underscores only (Bifrost forbids hyphens), at most 64.

    A staging entry is a separate Bifrost entry, so its name ends in `_stg`.
    """
    major, patch = version_key(version)
    suffix = f"_v{major}_{patch}" + (STAGING_SUFFIX if kind is RegistryKind.STAGING else "")
    name = server_name[: NAME_MAX - len(suffix)].rstrip("_") + suffix
    if MCP_SERVER_NAME.match(name) is None:
        raise NotRegistrable(f"{name!r} is not a valid registry name")
    return name


def to_model(row: RegisteredWorkflowRow) -> RegisteredWorkflow:
    return RegisteredWorkflow(
        workflow_id=row.workflow_id,
        version=row.version,
        registry_name=row.registry_name,
        registry_id=row.registry_id,
        image_digest=row.image_digest,
        manifest_hash=row.manifest_hash,
        certification_id=row.certification_id,
        owner=row.owner,
        tools=[McpToolEntry.model_validate(tool) for tool in row.tools],
        ui_bundle=UiBundleEntry.model_validate(row.ui_bundle) if row.ui_bundle else None,
        kind=RegistryKind(row.kind),
        status=RegistryStatus(row.status),
        registered_at=row.registered_at,
        superseded_at=row.superseded_at,
        superseded_by=row.superseded_by,
    )


@dataclass(frozen=True)
class Plan:
    """What would be registered, resolved from the frozen manifest alone (no Bifrost, no row)."""

    version: str
    registry_name: str
    image_digest: str
    manifest_hash: str
    ui_bundle: dict[str, Any] | None


@dataclass(frozen=True)
class _Resolved:
    workflow: Workflow
    manifest_hash: str
    certification_id: uuid.UUID
    manifest: WorkflowManifest
    server: McpServerEntry


def _resolve(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, version: str | None
) -> _Resolved:
    """The checks every registration passes (frozen, certified, built, UI bundle certified)."""
    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    if workflow.state != WorkflowState.FROZEN.value:
        raise NotRegistrable(f"the Workflow is {workflow.state}, not FROZEN")

    history = WorkflowManifestRepository(session, ctx).history(workflow_id)
    frozen = [row for row in history if row.is_frozen]
    if version is not None:
        frozen = [row for row in frozen if row.manifest.get("version") == version]
    if not frozen:
        of_version = f" of version {version}" if version else ""
        raise NotRegistrable(f"there is no frozen manifest{of_version}")
    row = max(frozen, key=lambda r: r.build_no)

    certificate = CertificationRepository(session, ctx).find(
        CertificationLevel.WORKFLOW.value, workflow_id, row.sha256
    )
    if certificate is None:
        raise NotRegistrable("there is no WORKFLOW certificate on this manifest's hash")
    manifest = WorkflowManifest.model_validate_json(json.dumps(row.manifest))
    server = manifest.mcp_server
    if server is None:
        raise NotRegistrable("the manifest has no MCP server: it was not built into an image")

    if manifest.requires_ui_bundle and manifest.ui_bundle is None:
        raise NotRegistrable("the manifest has no UI bundle: SRIM shows only BAM's bundle")
    if manifest.requires_ui_bundle:
        gates = GateResultRepository(session, ctx)
        reasons = ui_bundle.certification_reasons(
            manifest, gates.workflow_rows(workflow_id, GateName.UI_BUNDLE.value)
        )
        if reasons:
            raise NotRegistrable(f"the UI bundle is not certified ({reasons[0]})")
    return _Resolved(workflow, row.sha256, certificate.id, manifest, server)


def plan(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    kind: RegistryKind,
    version: str | None = None,
) -> Plan:
    """What `register` would register for `kind`, from the frozen manifest. Commit starts Staging
    from this before the Bifrost entry exists (#909): the container needs the certified digest,
    not a registry row."""
    found = _resolve(session, ctx, workflow_id, version)
    return Plan(
        version=found.server.version,
        registry_name=registry_name_for(found.server.name, found.server.version, kind),
        image_digest=found.server.image_digest,
        manifest_hash=found.manifest_hash,
        ui_bundle=found.manifest.ui_bundle.dump() if found.manifest.ui_bundle else None,
    )


def register(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    client: BifrostRegistry,
    *,
    kind: RegistryKind,
    owner: str,
    version: str | None = None,
    address_for: AddressFor = default_address,
    address: str | None = None,
    now: datetime | None = None,
) -> RegisteredWorkflow:
    """Register the frozen, certified manifest of a Workflow (the newest, or `version`) as `kind`.

    `kind` has no default on purpose: Commit (#766) passes STAGING, promotion (#148) PRODUCTION.
    `address` is the address Bifrost connects to when the caller knows where the server runs (the
    Staging container, #909); without it `address_for(name)` decides.
    """
    stamp = now or datetime.now(UTC)
    found = _resolve(session, ctx, workflow_id, version)
    workflow, manifest, server = found.workflow, found.manifest, found.server

    repo = RegisteredWorkflowRepository(session, ctx)
    existing = repo.of_version(workflow_id, server.version, kind)
    if existing is not None:
        if (
            existing.image_digest == server.image_digest
            and existing.manifest_hash == found.manifest_hash
        ):
            return to_model(existing)  # idempotent: Bifrost is not called
        raise RegistrationConflict(server.version)
    wanted = version_key(server.version)
    newer = [r for r in repo.of_workflow(workflow_id, kind) if version_key(r.version) > wanted]
    if newer:
        raise NotRegistrable(f"a newer version ({newer[0].version}) is already registered")

    name = registry_name_for(server.name, server.version, kind)
    entry = client.register(name, address or address_for(name))
    missing = sorted({tool.name for tool in server.tools} - set(entry.tools))
    if missing:  # the image is not what the manifest says: nothing uncertified may stay visible
        client.disable(entry.id)
        listed = ", ".join(missing)
        raise NotRegistrable(f"the server does not expose the manifest's tools: {listed}")

    created = RegisteredWorkflowRow(
        tenant_id=ctx.tenant_id,
        project_id=workflow.project_id,
        workflow_id=workflow_id,
        version=server.version,
        kind=kind.value,
        registry_name=name,
        registry_id=entry.id,
        image_digest=server.image_digest,
        manifest_hash=found.manifest_hash,
        certification_id=found.certification_id,
        owner=owner,
        tools=[tool.model_dump(mode="json") for tool in server.tools],
        ui_bundle=manifest.ui_bundle.dump() if manifest.ui_bundle else None,
        status=RegistryStatus.REGISTERED.value,
        registered_at=stamp,
    )
    repo.add(created)
    supersede_older(repo, client, created, stamp)
    return to_model(created)


def supersede_older(
    repo: RegisteredWorkflowRepository,
    client: BifrostRegistry,
    current: RegisteredWorkflowRow,
    stamp: datetime,
) -> list[str]:
    """Disable and mark every older enabled version of the same Workflow and the same kind.

    A disable that fails leaves that row REGISTERED (it still is enabled) for `reconcile`; the new
    entry is already registered. Returns the versions whose disable failed.
    """
    failed: list[str] = []
    for old in repo.of_workflow(current.workflow_id, RegistryKind(current.kind)):
        if old.id == current.id or old.status != RegistryStatus.REGISTERED.value:
            continue
        if version_key(old.version) >= version_key(current.version):
            continue
        try:
            client.disable(old.registry_id)
        except RegistryUnavailable:
            failed.append(old.version)
            continue
        old.status = RegistryStatus.SUPERSEDED.value
        old.superseded_at = stamp
        old.superseded_by = current.version
    return failed


def lookup(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    version: str | None = None,
    kind: RegistryKind | None = None,
) -> RegisteredWorkflow:
    """The entry of one version, or the newest enabled one, of `kind`.

    With no kind the assigned FDE sees both, staging before production. A consumer that holds a
    production key passes PRODUCTION and never sees a staging entry: that is a 404. A superseded
    version is still found, with its status, so a running consumer can look its own version up.
    Anything else, and anything of another tenant, is a 404.
    """
    repo = RegisteredWorkflowRepository(session, ctx)
    row = (
        repo.of_version(workflow_id, version, kind) if version else repo.current(workflow_id, kind)
    )
    if row is None:
        raise not_found()
    return to_model(row)


def names_for_key(session: Session, ctx: TenantContext, key: RegistryKind) -> list[str]:
    """The registry names a virtual key of `key` may be granted in Bifrost (runbook, item 3).

    SRIM's key is a PRODUCTION key: it is granted enabled production entries and never a staging
    one. The FDE's staging key is granted the enabled staging entries. The grant is by exact name
    from SBAM's table, never by a pattern, so a staging entry cannot leak into SRIM's key.
    """
    rows = RegisteredWorkflowRepository(session, ctx).all_rows()
    return sorted(
        row.registry_name
        for row in rows
        if row.kind == key.value and row.status == RegistryStatus.REGISTERED.value
    )


@dataclass
class ReconcileReport:
    reregistered: list[str] = field(default_factory=list)  # in the table, missing in Bifrost
    disabled: list[str] = field(default_factory=list)  # superseded, still enabled in Bifrost
    drift: list[str] = field(default_factory=list)  # current in the table, disabled in Bifrost


def reconcile(
    session: Session,
    ctx: TenantContext,
    client: BifrostRegistry,
    *,
    address_for: AddressFor = default_address,
) -> ReconcileReport:
    """Make Bifrost agree with SBAM's table.

    Re-registers what is missing and disables what is superseded. A current entry that is disabled
    in Bifrost is reported, not changed.
    """
    report = ReconcileReport()
    listed = {entry.name: entry for entry in client.entries()}
    for row in RegisteredWorkflowRepository(session, ctx).all_rows():
        entry = listed.get(row.registry_name)
        if row.status == RegistryStatus.REGISTERED.value:
            if entry is None:
                again = client.register(row.registry_name, address_for(row.registry_name))
                row.registry_id = again.id
                report.reregistered.append(row.registry_name)
            elif entry.disabled:
                report.drift.append(row.registry_name)
        elif entry is not None and not entry.disabled:
            client.disable(entry.id)
            report.disabled.append(row.registry_name)
    return report
