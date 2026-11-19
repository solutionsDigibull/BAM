"""The activities of `WorkflowCommit` (#766, ADR 0032): recheck, freeze, register, start Staging.

Each is idempotent, so a crash between any two is finished by running Commit again (WG18-T05):

- `commit_recheck`  the same checks the API ran, on the hash the FDE approved (stale: refused);
- `commit_freeze`   `WorkflowFreezer.freeze` (no-op when the Workflow is already FROZEN);
- `commit_register` `service.register(kind=STAGING)` on the manifest's certified digest: no rebuild,
                    and the same version with the same digest is a no-op (Bifrost is not called);
- `commit_start_staging` writes the audit row of who approved, then calls the `StagingRuntime` port
                    (#765) with the registered digest and the secret NAMES. The values are never
                    read here: the runtime asks `connections.inject.resolve_env` itself.

A refusal (a failed check, a stale hash, a digest conflict) is non-retryable and ends the run with
its reason; Bifrost being down is retried.
"""

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from sbam_api.connections.crypto import KeyRing
from sbam_api.core.conventions import ApiError
from sbam_api.core.settings import deployed_env
from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.factory import make_backend
from sbam_api.workflow_commit import service
from sbam_api.workflow_commit.ports import (
    StagingRuntime,
    StateMachineFreezer,
    WorkflowFreezer,
    default_runtime,
)
from sbam_api.workflow_registry import service as registry
from sbam_api.workflow_registry.bifrost import (
    BifrostRegistry,
    HttpBifrostRegistry,
    RegistryUnavailable,
)
from sbam_contracts.pipeline import IdResult
from sbam_contracts.runs import TaskQueue
from sbam_contracts.workflow_commit import (
    StagingStartResult,
    StagingStartStatus,
    WorkflowCommitInput,
    WorkflowCommitResult,
)
from sbam_contracts.workflow_registry import RegistryKind
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings
from sbam_workers.staging.deployer import make_deployer
from sbam_workers.staging.egress_proxy import PolicyDir
from sbam_workers.staging.runtime import DeployerStagingRuntime, StagingConfig

REFUSED = "CommitRefused"
STAGING_FAILED = "StagingStartFailed"


def refused(error: ApiError) -> ApplicationError:
    """A 4xx answer is a verdict, not a fault: it is never retried."""
    return ApplicationError(
        f"{error.title}: {error.detail or ''}"[:500], type=REFUSED, non_retryable=True
    )


class CommitSteps:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage: StorageBackend,
        bifrost: Callable[[], BifrostRegistry],
        freezer: WorkflowFreezer,
        runtime: StagingRuntime,
    ) -> None:
        self.sessions = sessions
        self.storage = storage
        self.bifrost = bifrost
        self.freezer = freezer
        self.runtime = runtime

    @staticmethod
    def _ctx(inp: WorkflowCommitInput) -> TenantContext:
        return TenantContext(inp.tenant_id, inp.project_id)

    def recheck(self, inp: WorkflowCommitInput) -> IdResult:
        with self.sessions() as session:
            try:
                service.recheck(
                    session, self._ctx(inp), inp.workflow_id, inp.manifest_hash, self.storage
                )
            except ApiError as error:
                raise refused(error) from None
        return IdResult(id=inp.workflow_id)

    def freeze(self, inp: WorkflowCommitInput) -> IdResult:
        with self.sessions() as session:
            try:
                self.freezer.freeze(
                    session,
                    self._ctx(inp),
                    inp.workflow_id,
                    inp.manifest_hash,
                    inp.correlation_id,
                    self.storage,
                )
                session.commit()
            except ApiError as error:
                session.rollback()
                raise refused(error) from None
        return IdResult(id=inp.workflow_id)

    def register(self, inp: WorkflowCommitInput) -> IdResult:
        with self.sessions() as session:
            ctx = self._ctx(inp)
            try:
                entry = registry.register(
                    session,
                    ctx,
                    inp.workflow_id,
                    self.bifrost(),
                    kind=RegistryKind.STAGING,
                    owner=inp.actor_id,
                )
                service.record_committed(
                    session, ctx, inp.workflow_id, inp, entry.registry_name, entry.image_digest
                )
                session.commit()
            except RegistryUnavailable:
                session.rollback()
                raise  # Bifrost is down or refused the token: retried with backoff
            except ApiError as error:
                session.rollback()
                raise refused(error) from None
        return IdResult(id=inp.workflow_id)

    def start_staging(self, inp: WorkflowCommitInput) -> WorkflowCommitResult:
        with self.sessions() as session:
            ctx = self._ctx(inp)
            try:
                request = service.staging_request(session, ctx, inp.workflow_id, inp.actor_id)
            except ApiError as error:
                raise refused(error) from None
            result = self.runtime.start(request)
            service.record_staging(session, ctx, inp.workflow_id, inp, result)
            session.commit()
        if result.status is StagingStartStatus.FAILED:
            raise ApplicationError(
                f"the Staging runtime could not start it: {result.detail or 'no reason given'}",
                type=STAGING_FAILED,
                non_retryable=True,
            )
        return WorkflowCommitResult(
            workflow_id=inp.workflow_id,
            version=request.version,
            registry_name=request.registry_name,
            image_digest=request.image_digest,
            manifest_hash=request.manifest_hash,
            staging=StagingStartResult(status=result.status, detail=result.detail),
        )


def make_registry(settings: WorkerSettings) -> BifrostRegistry:
    token: SecretStr | None = settings.bifrost_setup_token
    if token is None:
        raise RegistryUnavailable("the Bifrost setup token is not configured (BIFROST_SETUP_TOKEN)")
    return HttpBifrostRegistry(str(settings.bifrost_url), token.get_secret_value())


def make_runtime(
    settings: WorkerSettings, sessions: sessionmaker[Session], storage: StorageBackend
) -> StagingRuntime:
    """The Staging runtime #765 built, or the pending one when no Staging host is configured (the
    default: `make up` stays healthy and Commit records PENDING_RUNTIME)."""
    if settings.staging_runtime != "docker":
        return default_runtime()
    keys = settings.secrets_keys

    def keyring() -> KeyRing | None:
        if keys is None:
            return None
        return KeyRing.parse(
            keys.get_secret_value(), allow_placeholder=not deployed_env(settings.sbam_env)
        )

    folder = settings.staging_egress_policy_dir
    return DeployerStagingRuntime(
        sessions,
        storage,
        keyring,
        make_deployer(settings.staging_runtime, settings.staging_docker_timeout_seconds),
        StagingConfig(
            network=settings.staging_network,
            proxy_url=settings.staging_egress_proxy_url,
            policy_dir=None if folder is None else PolicyDir(Path(folder)),
        ),
    )


@lru_cache(maxsize=1)
def _steps() -> CommitSteps:
    settings = WorkerSettings()  # type: ignore[call-arg]
    return CommitSteps(
        mirror.sessions(),
        make_backend(settings),
        lambda: make_registry(settings),
        StateMachineFreezer(),
        make_runtime(settings, mirror.sessions(), make_backend(settings)),
    )


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="commit_recheck")
def commit_recheck(inp: WorkflowCommitInput) -> IdResult:
    return _steps().recheck(inp)


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="commit_freeze")
def commit_freeze(inp: WorkflowCommitInput) -> IdResult:
    return _steps().freeze(inp)


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="commit_register")
def commit_register(inp: WorkflowCommitInput) -> IdResult:
    return _steps().register(inp)


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name="commit_start_staging")
def commit_start_staging(inp: WorkflowCommitInput) -> WorkflowCommitResult:
    return _steps().start_staging(inp)
