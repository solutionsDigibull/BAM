"""`export_version` (#133, E14.5): package one frozen Workflow version.

1. idempotent: a run that already exported (its `EXPORTED` audit row) answers what it stored;
2. collect the entries of the FROZEN version (409 `NOT_FROZEN` otherwise, non-retryable);
3. re-read and hash every frozen file with #132's `read_back`. A mismatch aborts: an
   `EXPORT_HASH_MISMATCH` audit row (the alert) and an error log, no zip, non-retryable;
4. write the deterministic zip to a temp file and store it through `StorageBackend` (kind
   `exports`, content-addressed: the same version always gives the same key);
5. index it as a frozen `artifacts` row of kind EXPORT with its sha256, write the `EXPORTED` audit
   row and the in-app 'Export ready' notification, in one transaction.
"""

import logging
import tempfile
from functools import lru_cache
from typing import BinaryIO, cast

from sbam_api.artifacts.models import ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.audit.repository import find_by_key, record
from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.exports.service import ENTITY, EXPORT_FAILED, EXPORTED
from sbam_api.notifications.service import Source, notify_project
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_api.versioning.freezer import HashMismatch, read_back
from sbam_contracts.exports import EXPORT_ACTIVITY, ExportInput, ExportResult, export_workflow_id
from sbam_contracts.notifications import NotificationType
from sbam_contracts.runs import TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.exports.collect import collect
from sbam_workers.exports.package import UnsafePath, write_package
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger(__name__)
REFUSED = "ExportRefused"
HASH_MISMATCH = "HashMismatch"


class ExportSteps:
    def __init__(self, sessions: sessionmaker[Session], storage: StorageBackend) -> None:
        self.sessions = sessions
        self.storage = storage

    def run(self, inp: ExportInput) -> ExportResult:
        ctx = TenantContext(inp.tenant_id, inp.project_id)
        done_key = f"{EXPORTED}:{inp.run_id}"
        with self.sessions() as session:
            prior = find_by_key(session, ctx.tenant_id, ENTITY, str(inp.workflow_id), done_key)
            if prior is not None and prior.after is not None:
                return ExportResult.model_validate(prior.after["result"])
            try:
                collected = collect(session, ctx, inp.workflow_id, inp.version, self.storage)
                read_back(self.storage, collected.files)
            except HashMismatch as error:
                self._alert(session, ctx, inp, error.detail or "hash mismatch")
                raise ApplicationError(
                    f"HashMismatch: {error.detail}"[:500], type=HASH_MISMATCH, non_retryable=True
                ) from None
            except ApiError as error:
                raise ApplicationError(
                    f"{error.title}: {error.detail or ''}"[:500], type=REFUSED, non_retryable=True
                ) from None
            except UnsafePath as error:  # a bundle file named .env, '..', ...: never retried
                raise ApplicationError(str(error), type=REFUSED, non_retryable=True) from None
            try:
                with tempfile.TemporaryFile() as sink:
                    info = write_package(collected.entries, sink)
                    stored = self.storage.put(
                        cast(BinaryIO, sink),
                        StorageScope(ctx.tenant_id, inp.project_id, inp.workflow_id),
                        Kind.EXPORTS,
                        "application/zip",
                    )
            except HashMismatch as error:  # a lazily read file (schema.sql) did not match
                self._alert(session, ctx, inp, error.detail or "hash mismatch")
                raise ApplicationError(
                    f"HashMismatch: {error.detail}"[:500], type=HASH_MISMATCH, non_retryable=True
                ) from None
            except UnsafePath as error:
                raise ApplicationError(str(error), type=REFUSED, non_retryable=True) from None
            artifact = ArtifactRepository(session, ctx).index(
                ArtifactKind.EXPORT, stored, inp.actor_id
            )
            if not artifact.is_frozen:
                artifact.is_frozen = True
            result = ExportResult(
                workflow_id=inp.workflow_id,
                version=inp.version,
                storage_key=stored.storage_key,
                sha256=info.sha256,
                size=info.size,
                entries=len(info.entries),
            )
            record(
                session,
                tenant_id=ctx.tenant_id,
                actor_id=inp.actor_id,
                action=EXPORTED,
                entity_type=ENTITY,
                entity_id=str(inp.workflow_id),
                correlation_id=inp.correlation_id,
                after={
                    "version": inp.version,
                    "storage_key": stored.storage_key,
                    "sha256": info.sha256,
                    "size": info.size,
                    "run_id": str(inp.run_id),
                    "result": result.model_dump(mode="json"),
                },
                idempotency_key=done_key,
            )
            notify_project(
                session,
                ctx.tenant_id,
                type=NotificationType.EXPORT_READY,
                project_id=inp.project_id,
                source=Source(export_workflow_id(inp.run_id), EXPORT_ACTIVITY),
                workflow_id=inp.workflow_id,
                params={"version": inp.version},
            )
            session.commit()
        return result

    @staticmethod
    def _alert(session: Session, ctx: TenantContext, inp: ExportInput, reason: str) -> None:
        session.rollback()
        log.error(
            "export of workflow %s version %s aborted: %s", inp.workflow_id, inp.version, reason
        )
        key = f"{EXPORT_FAILED}:{inp.run_id}"
        if find_by_key(session, ctx.tenant_id, ENTITY, str(inp.workflow_id), key) is None:
            record(
                session,
                tenant_id=ctx.tenant_id,
                actor_id=inp.actor_id,
                action=EXPORT_FAILED,
                entity_type=ENTITY,
                entity_id=str(inp.workflow_id),
                correlation_id=inp.correlation_id,
                after={"version": inp.version, "run_id": str(inp.run_id), "reason": reason[:500]},
                idempotency_key=key,
            )
            session.commit()


@lru_cache(maxsize=1)
def _steps() -> ExportSteps:
    settings = WorkerSettings()  # type: ignore[call-arg]
    return ExportSteps(mirror.sessions(), make_backend(settings))


@activity_for(TaskQueue.CERTIFY)
@activity.defn(name=EXPORT_ACTIVITY)
def export_version(inp: ExportInput) -> ExportResult:
    return _steps().run(inp)
