"""The real `StagingRuntime` (#765, WG-17; ADR 0032, ADR 0036): start the committed workflow's
container by its certified digest, and keep one Staging deployment per project and version.

`start` is what `commit_start_staging` calls after Commit froze the workflow and before it
registers it in Bifrost (ADR 0039, #909). In order:

1. The request's image digest and manifest hash must be the frozen manifest's
   `mcp_server.image_digest` and hash, with a UI bundle the bundle digest too (a STAGING registry
   entry of that version, when one already exists, must agree and be REGISTERED). Any difference
   is refused (`DIGEST_MISMATCH`): the container is never started on an image that was not
   certified. The stored UI bundle must still match its digest.
2. The same version and digest that already runs is answered as running (idempotent).
3. Secrets: `connections.inject.resolve_env`, the single audited point, called with the NAMES in the
   request. The values go to the deployer and nowhere else (not a log, trace, audit row or
   manifest).
4. The deployer starts the digest on the Staging network, and the runtime waits (up to
   `ready_timeout_seconds`) until the server answers on its address, so Bifrost can list its tools
   when Commit registers it next. A server that never answers is stopped and refused
   (`SERVER_NOT_READY`). With an egress proxy configured the
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
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from sbam_api.audit.repository import record
from sbam_api.connections.crypto import KeyRing
from sbam_api.connections.inject import resolve_env
from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.staging import records
from sbam_api.staging.service import verified_bundle_files
from sbam_api.storage.base import StorageBackend
from sbam_api.workflow_registry import service as registry
from sbam_api.workflow_registry.repository import RegisteredWorkflowRepository
from sbam_contracts.staging import StagingState
from sbam_contracts.workflow_commit import (
    StagingStartRequest,
    StagingStartResult,
    StagingStartStatus,
)
from sbam_contracts.workflow_registry import RegistryKind, RegistryStatus
from sqlalchemy.orm import Session, sessionmaker

from sbam_workers.staging.deployer import ContainerDeployer, Deployed, DeployError, DeploySpec
from sbam_workers.staging.egress_proxy import PolicyDir

log = logging.getLogger(__name__)

WORKER: Final = "sbam-staging-runtime"
PURPOSE: Final = "staging-start"
MISMATCH: Final = "DIGEST_MISMATCH"
BUNDLE_CHANGED: Final = "UI_BUNDLE_CHANGED"
NO_KEYS: Final = "SECRETS_KEY_MISSING"
NOT_READY: Final = "SERVER_NOT_READY"


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
    ready_timeout_seconds: int = 0  # wait this long for the server to answer; 0 = do not wait


def http_ready(endpoint: str) -> bool:
    """Any answer below 500 means the workflow server is up (its MCP endpoint answers a plain GET
    with 4xx)."""
    try:
        answer = httpx.get(endpoint.rstrip("/") + "/mcp", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code < 500


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
        ready: Callable[[str], bool] = http_ready,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.keyring = keyring
        self.deployer = deployer
        self.config = config or StagingConfig()
        self.ready = ready
        self.sleep = sleep
        self.clock = clock

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
        try:
            planned = registry.plan(session, ctx, request.workflow_id, RegistryKind.STAGING)
        except ApiError as error:
            raise Refused(MISMATCH, (error.detail or error.title)[:200]) from None
        if (planned.version, planned.image_digest, planned.manifest_hash) != (
            request.version,
            request.image_digest,
            request.manifest_hash,
        ):
            raise Refused(MISMATCH, "the request is not the frozen manifest's certified image")
        entry = RegisteredWorkflowRepository(session, ctx).of_version(
            request.workflow_id, request.version, RegistryKind.STAGING
        )
        if entry is not None and (
            entry.status != RegistryStatus.REGISTERED.value
            or entry.image_digest != planned.image_digest
            or entry.manifest_hash != planned.manifest_hash
        ):
            raise Refused(MISMATCH, "the image digest differs from the registry or the manifest")
        bundle = planned.ui_bundle
        digests = {None if bundle is None else str(bundle["digest"]), request.ui_bundle_digest}
        if entry is not None:
            digests.add(None if not entry.ui_bundle else str(entry.ui_bundle["digest"]))
        if len(digests) != 1:
            raise Refused(
                MISMATCH, "the UI bundle digest differs from the registry or the manifest"
            )
        if bundle is None:
            return []
        try:
            verified_bundle_files(session, ctx, request.workflow_id, self.storage, bundle)
        except ApiError:
            raise Refused(
                BUNDLE_CHANGED, "the stored UI bundle no longer matches its digest"
            ) from None
        return list(bundle.get("allowed_hosts", []))

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
            self._await_ready(deployed)
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

    def _await_ready(self, deployed: Deployed) -> None:
        """Wait for the server to answer, so Bifrost can list its tools right after. A server that
        never does is stopped and refused: nothing is left running that no record shows."""
        wait = self.config.ready_timeout_seconds
        if wait <= 0:
            return
        deadline = self.clock() + wait
        while not self.ready(deployed.endpoint):
            if self.clock() >= deadline:
                try:
                    self.deployer.stop(deployed.container)
                except DeployError as error:
                    log.warning("staging: could not stop %s: %s", deployed.container, error.code)
                raise Refused(NOT_READY, f"the server did not answer within {wait} seconds")
            self.sleep(1.0)

    def abandon(self, request: StagingStartRequest, detail: str) -> None:
        """Commit stopped after the start: stop this version's container and record it FAILED."""
        with self.sessions() as session:
            found = records.replay(session, request.tenant_id, request.workflow_id).get(
                request.version
            )
        if found is not None and found.started is not None:
            try:
                self.deployer.stop(found.started.container)
            except DeployError as error:
                detail = f"{detail} (container not stopped: {error.code})"
        self._failed(request, detail)

    def _egress(self, name: str, hosts: list[str]) -> tuple[dict[str, str], bool]:
        """The proxy settings of the container and whether the allowlist is enforced. No proxy
        configured: the container sits on the internal network with no way out at all."""
        proxy, policies = self.config.proxy_url, self.config.policy_dir
        if not proxy or policies is None:
            return {}, False
        token = secrets.token_hex(16)
        policies.write(token, name, hosts)
        parts = urlsplit(proxy)
        # `token:x`, not `token@`: urllib and requests send no Proxy-Authorization for a user name
        # without a password: the proxy answered 403 "unknown deployment token" (live stack, #908).
        url = f"{parts.scheme}://{token}:x@{parts.netloc}"
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
