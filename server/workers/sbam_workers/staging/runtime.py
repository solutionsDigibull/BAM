"""The real `StagingRuntime` (#765, WG-17; ADR 0032, ADR 0036): start the committed workflow's
container by its certified digest, and keep one Staging deployment per project and version.

`start` is what `commit_start_staging` calls after Commit froze and registered the workflow. In
order:

1. The STAGING entry of that version must still be the current one (REGISTERED), and its image
   digest,
   the request's and the frozen manifest's `mcp_server.image_digest` (and, with a UI bundle, the
   bundle digest) must all be the same. Any difference is refused (`DIGEST_MISMATCH`): the
   container is
   never started on an image that was not certified. The stored UI bundle must still match its
   digest.
2. The same version and digest that already runs is answered as running (idempotent).
3. Secrets: `connections.inject.resolve_env`, the single audited point, called with the NAMES in the
   request. The values go to the deployer and nowhere else (not a log, trace, audit row or
   manifest).
4. The deployer starts the digest on the Staging network. With an egress proxy configured the
   container's only way out is through it, with the allowlist the certified manifest declares
   (`ui_bundle.allowed_hosts`); without one the container has no outside access at all.
5. The deployment is recorded (`staging_deployment_started`), then every other running deployment
   of the Workflow is stopped and recorded `SUPERSEDED` (kept on record, never deleted). No time
   limit:
   nothing here ever stops a deployment on a timer.

A refusal or a deployer error is recorded (`staging_deployment_failed`) and answered `FAILED`;
running
Commit again retries. `DEPLOYER_DISABLED` answers `PENDING_RUNTIME` so `make up` stays healthy.
"""

import logging
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from pydantic import SecretStr
from sbam_api.audit.repository import record
from sbam_api.connections.crypto import KeyRing
from sbam_api.connections.inject import resolve_env
from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import WorkflowManifestRepository
from sbam_api.staging import records
from sbam_api.staging.service import bundle_files
from sbam_api.storage.base import StorageBackend
from sbam_api.workflow_registry.repository import RegisteredWorkflowRepository
from sbam_contracts.manifest import WorkflowManifest
from sbam_contracts.staging import StagingState
from sbam_contracts.workflow_commit import (
    StagingStartRequest,
    StagingStartResult,
    StagingStartStatus,
)
from sbam_contracts.workflow_registry import RegistryKind, RegistryStatus
from sqlalchemy.orm import Session, sessionmaker

from sbam_workers.staging.deployer import ContainerDeployer, DeployError, DeploySpec
from sbam_workers.staging.egress_proxy import PolicyDir

log = logging.getLogger(__name__)

WORKER: Final = "sbam-staging-runtime"
PURPOSE: Final = "staging-start"
MISMATCH: Final = "DIGEST_MISMATCH"
BUNDLE_CHANGED: Final = "UI_BUNDLE_CHANGED"
NO_KEYS: Final = "SECRETS_KEY_MISSING"


class Refused(Exception):  # noqa: N818 - the reason, in plain words
    """The start was refused before anything ran; `code` is stable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StagingConfig:
    network: str = "sbam-staging"
    proxy_url: str | None = None  # the egress proxy on the Staging network, `http://host:port`
    policy_dir: PolicyDir | None = None  # where the proxy reads each deployment's allowlist


def container_name(workflow_id: uuid.UUID, registry_name: str) -> str:
    """One container per project and version. The registry name is unique per tenant only, so the
    first eight characters of the workflow id keep two tenants' names apart on a shared host."""
    return f"stg-{workflow_id.hex[:8]}-{registry_name.replace('_', '-')}"[:63].rstrip("-")


class DeployerStagingRuntime:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage: StorageBackend,
        keyring: Callable[[], KeyRing | None],
        deployer: ContainerDeployer,
        config: StagingConfig | None = None,
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.keyring = keyring
        self.deployer = deployer
        self.config = config or StagingConfig()

    # --- the port --------------------------------------------------------------------------

    def start(self, request: StagingStartRequest) -> StagingStartResult:
        try:
            detail = self._start(request)
        except Refused as refusal:
            return self._failed(request, f"{refusal.code}: {refusal.message}")
        except DeployError as error:
            if error.code == "DEPLOYER_DISABLED":
                return StagingStartResult(
                    status=StagingStartStatus.PENDING_RUNTIME, detail=error.message[:300]
                )
            return self._failed(request, f"{error.code}: {error.message}")
        return StagingStartResult(status=StagingStartStatus.STARTED, detail=detail)

    # --- steps -----------------------------------------------------------------------------

    def _failed(self, request: StagingStartRequest, detail: str) -> StagingStartResult:
        detail = detail[:300]
        with self.sessions() as session:
            record(
                session,
                tenant_id=request.tenant_id,
                actor_id=WORKER,
                action=records.FAILED,
                entity_type=records.ENTITY,
                entity_id=str(request.workflow_id),
                correlation_id=str(uuid7()),
                after={
                    "version": request.version,
                    "registry_name": request.registry_name,
                    "image_digest": request.image_digest,
                    "detail": detail,
                },
            )
            session.commit()
        return StagingStartResult(status=StagingStartStatus.FAILED, detail=detail)

    def _verify(
        self, session: Session, ctx: TenantContext, request: StagingStartRequest
    ) -> list[str]:
        """The certified allowlist (the manifest's declared hosts), after every digest agrees."""
        entry = RegisteredWorkflowRepository(session, ctx).current(
            request.workflow_id, RegistryKind.STAGING
        )
        if (
            entry is None
            or entry.version != request.version
            or entry.status != RegistryStatus.REGISTERED.value
        ):
            raise Refused(MISMATCH, "the version is not the current Staging entry")
        frozen = next(
            (
                row
                for row in WorkflowManifestRepository(session, ctx).history(request.workflow_id)
                if row.is_frozen
            ),
            None,
        )
        if frozen is None:
            raise Refused(MISMATCH, "the Workflow has no frozen manifest")
        manifest = WorkflowManifest.model_validate(frozen.manifest)
        certified = manifest.mcp_server.image_digest if manifest.mcp_server else None
        if len({request.image_digest, entry.image_digest, certified}) != 1:
            raise Refused(MISMATCH, "the image digest differs from the registry or the manifest")
        if frozen.sha256 != entry.manifest_hash or request.manifest_hash != entry.manifest_hash:
            raise Refused(MISMATCH, "the manifest hash differs from the registry entry")
        bundle = manifest.ui_bundle
        wanted = None if bundle is None else bundle.digest
        recorded = None if not entry.ui_bundle else str(entry.ui_bundle["digest"])
        if len({wanted, recorded, request.ui_bundle_digest}) != 1:
            raise Refused(
                MISMATCH, "the UI bundle digest differs from the registry or the manifest"
            )
        if bundle is not None:
            try:
                bundle_files(session, ctx, request.workflow_id, self.storage, entry)
            except ApiError:
                raise Refused(
                    BUNDLE_CHANGED, "the stored UI bundle no longer matches its digest"
                ) from None
        return [] if bundle is None else list(bundle.allowed_hosts)

    def _start(self, request: StagingStartRequest) -> str | None:
        ctx = TenantContext(request.tenant_id, request.project_id)
        with self.sessions() as session:
            hosts = self._verify(session, ctx, request)
            existing = records.replay(session, request.tenant_id, request.workflow_id).get(
                request.version
            )
            running = (
                existing is not None
                and existing.state is StagingState.RUNNING
                and existing.started is not None
                and existing.started.image_digest == request.image_digest
                and self.deployer.is_running(existing.started.container)
            )
            if running:
                self._supersede(session, request)
                return "already running"
            values: dict[str, SecretStr] = {}
            if request.secret_names:
                keyring = self.keyring()
                if keyring is None:
                    raise Refused(NO_KEYS, "the worker has no SECRETS_KEYS to open the secrets")
                values = resolve_env(
                    session,
                    ctx,
                    request.workflow_id,
                    keyring=keyring,
                    actor_id=request.requested_by,
                    purpose=PURPOSE,
                    run_id=uuid.uuid4(),
                    names=request.secret_names,
                )
            name = container_name(request.workflow_id, request.registry_name)
            plain, enforced = self._egress(name, hosts)
            deployed = self.deployer.start(
                DeploySpec(
                    name=name,
                    image_digest=request.image_digest,
                    env=values,
                    plain_env=plain,
                    network=self.config.network,
                )
            )
            record(
                session,
                tenant_id=request.tenant_id,
                actor_id=WORKER,
                action=records.STARTED,
                entity_type=records.ENTITY,
                entity_id=str(request.workflow_id),
                correlation_id=str(uuid7()),
                after=records.StartedRecord(
                    version=request.version,
                    registry_name=request.registry_name,
                    image_digest=request.image_digest,
                    manifest_hash=request.manifest_hash,
                    ui_bundle_digest=request.ui_bundle_digest,
                    container=deployed.container,
                    endpoint=deployed.endpoint,
                    egress_hosts=hosts,
                    egress_enforced=enforced,
                    secret_names=sorted(values),
                ).model_dump(mode="json"),
            )
            session.commit()
            note = self._supersede(session, request)
        log.info("staging started: %s (%d secrets injected)", request.registry_name, len(values))
        return note

    def _egress(self, name: str, hosts: list[str]) -> tuple[dict[str, str], bool]:
        """The proxy settings of the container and whether the allowlist is enforced. No proxy
        configured: the container sits on the internal network with no way out at all."""
        proxy, policies = self.config.proxy_url, self.config.policy_dir
        if not proxy or policies is None:
            return {}, False
        token = secrets.token_hex(16)
        policies.write(token, name, hosts)
        parts = urlsplit(proxy)
        url = f"{parts.scheme}://{token}@{parts.netloc}"
        no_proxy = f"localhost,127.0.0.1,{name}"
        return {
            "HTTP_PROXY": url,
            "HTTPS_PROXY": url,
            "http_proxy": url,
            "https_proxy": url,
            "NO_PROXY": no_proxy,
            "no_proxy": no_proxy,
            "SBAM_EGRESS_ALLOWLIST": ",".join(hosts),
        }, True

    def _supersede(self, session: Session, request: StagingStartRequest) -> str | None:
        """Stop every other running deployment of this Workflow and record it SUPERSEDED."""
        problems: list[str] = []
        deployed = records.replay(session, request.tenant_id, request.workflow_id)
        for old in records.running(deployed):
            if old.version == request.version or old.started is None:
                continue
            try:
                self.deployer.stop(old.started.container)
            except DeployError as error:
                log.warning("staging: could not stop %s: %s", old.version, error.code)
                problems.append(f"{old.version} not stopped ({error.code})")
                continue
            record(
                session,
                tenant_id=request.tenant_id,
                actor_id=WORKER,
                action=records.STOPPED,
                entity_type=records.ENTITY,
                entity_id=str(request.workflow_id),
                correlation_id=str(uuid7()),
                after={
                    "version": old.version,
                    "reason": records.SUPERSEDED,
                    "superseded_by": request.version,
                },
            )
        session.commit()
        return "; ".join(problems)[:300] or None
