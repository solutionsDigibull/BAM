"""Commit (#766): the API side (status, the request) and the activities' steps.

Order of `POST W/commit` (nothing is stored before the checks pass):

1. 404 for a Workflow outside this tenant and project (the route's guard and the scoped repository);
   428 without `If-Match`; 409 `VERSION_CONFLICT` when the hash in `If-Match` is not the Workflow's
   current combined manifest hash (the SOW approval pattern: the FDE approved what they saw).
2. 409 with every reason when a check fails (certification, Sub-Skills, server, UI bundle) or a
   required secret is missing or not tested. A Workflow that is already frozen (a half-done Commit)
   is checked the same way, so a re-run completes it and nothing else.
3. The same `Idempotency-Key` answers the run it started (422 when it was used for another hash).
4. The approval is written to the audit trail (who, which hash), then `WorkflowCommit` is started
   with `USE_EXISTING`: a Commit that is running is not started twice.

The steps are idempotent, so a crash between any two of them is finished by running Commit again:
freeze (no-op when FROZEN), register (no-op for the same digest) and start Staging (the runtime
answers the running container).
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence

from sbam_contracts.api import ErrorCode
from sbam_contracts.runs import Run, SubjectType, TaskQueue
from sbam_contracts.workflow_commit import (
    MAX_REASONS,
    WORKFLOW_COMMIT,
    CommitAccepted,
    CommitState,
    CommitStatus,
    StagingStartRequest,
    StagingStartResult,
    WorkflowCommitInput,
    workflow_commit_id,
)
from sbam_contracts.workflow_registry import RegistryKind
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import entity_history, find_by_key, record
from sbam_api.connections import service as connections
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.pipeline.service import TemporalUnavailable
from sbam_api.storage.base import StorageBackend
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.temporal.service import StartSpec, start_run
from sbam_api.workflow_commit import checks
from sbam_api.workflow_commit.checks import Evaluation
from sbam_api.workflow_registry import service as registry
from sbam_api.workflow_registry.repository import RegisteredWorkflowRepository
from sbam_api.workflows.repository import WorkflowRepository

ENTITY = "workflow"
REQUESTED = "WORKFLOW_COMMIT_REQUESTED"
COMMITTED = "WORKFLOW_COMMITTED"
STAGING = "WORKFLOW_COMMIT_STAGING"
KEY_PREFIX = "workflow-commit:"
WORKER = "sbam-workflow-commit"


class CommitRefused(ApiError):  # noqa: N818 - the reason, in plain words
    def __init__(self, reasons: list[str]) -> None:
        super().__init__(
            409,
            ErrorCode.CONFLICT,
            "Commit is refused",
            "; ".join(reasons)[:1_500] or "a check failed",
        )
        self.reasons = reasons


def stale() -> ApiError:
    return ApiError(
        409,
        ErrorCode.VERSION_CONFLICT,
        "The Workflow changed",
        "The version you reviewed is not the current one; reload it before committing.",
    )


def hash_of_if_match(if_match: str | None) -> str:
    if not if_match or not if_match.strip():
        raise ApiError(
            428, ErrorCode.PRECONDITION_REQUIRED, "If-Match header required on this write"
        )
    return if_match.strip().removeprefix("W/").strip('"')


def etag_of(manifest_hash: str | None) -> str:
    return f'"{manifest_hash or "none"}"'


def _latest(rows: Sequence[AuditLog], action: str) -> AuditLog | None:
    found = [r for r in rows if r.action == action]
    return found[-1] if found else None


def status(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, storage: StorageBackend
) -> CommitStatus:
    """What the Validation screen shows. Read-only."""
    evaluation = checks.evaluate(session, ctx, workflow_id, storage)
    history = entity_history(session, ctx.tenant_id, ENTITY, str(workflow_id))
    entry = RegisteredWorkflowRepository(session, ctx).current(workflow_id, RegistryKind.STAGING)
    registration = None if entry is None else registry.to_model(entry)
    current = evaluation.manifest_hash
    if registration is not None and registration.manifest_hash != current:
        registration = None  # a Staging entry of an older manifest is not this Commit
    committed = _latest(history, COMMITTED)
    staged = _latest(history, STAGING)
    requested = _latest(history, REQUESTED)
    run_row = TemporalRunRepository(session, ctx).latest_for_workflow_id(
        workflow_commit_id(workflow_id)
    )
    if registration is not None:
        state = CommitState.COMMITTED
    elif (
        requested is not None
        and str((requested.after or {}).get("manifest_hash")) in evaluation.lineage
    ):
        state = CommitState.COMMITTING
    else:
        state = CommitState.NOT_COMMITTED
    staging = None
    if staged is not None and registration is not None:
        after = staged.after or {}
        recorded = after.get("manifest_hash")
        if recorded == registration.manifest_hash or recorded in evaluation.lineage:
            staging = StagingStartResult.model_validate(
                {"status": after["status"], "detail": after.get("detail")}
            )
    done = registration is not None and committed is not None
    return CommitStatus(
        workflow_id=workflow_id,
        manifest_hash=current,
        ready=evaluation.ready,
        checks=evaluation.checks,
        blockers=evaluation.blockers[:MAX_REASONS],
        state=state,
        registration=registration,
        staging=staging,
        committed_by=committed.actor_id if done and committed is not None else None,
        committed_at=committed.at if done and committed is not None else None,
        run=None if run_row is None else to_run(run_row),
    )


def _approve(
    sessions: sessionmaker[Session],
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    manifest_hash: str,
    key: str,
    correlation_id: str,
) -> uuid.UUID:
    """Checks and the approval row, committed. Returns the tenant id."""
    ctx = user.context(project_id)
    with sessions() as session:
        evaluation = checks.evaluate(session, ctx, workflow_id, storage)
        prior = find_by_key(session, ctx.tenant_id, ENTITY, str(workflow_id), KEY_PREFIX + key)
        if prior is not None:
            if (prior.after or {}).get("manifest_hash") != manifest_hash:
                raise ApiError(
                    422,
                    ErrorCode.IDEMPOTENCY_KEY_REUSED,
                    "Idempotency-Key already used with a different request",
                )
            return ctx.tenant_id
        if not evaluation.approved(manifest_hash):
            raise stale()
        if not evaluation.ready:
            raise CommitRefused(evaluation.reasons())
        record(
            session,
            tenant_id=ctx.tenant_id,
            actor_id=str(user.user_id),
            action=REQUESTED,
            entity_type=ENTITY,
            entity_id=str(workflow_id),
            correlation_id=correlation_id,
            after={"manifest_hash": manifest_hash, "version": evaluation.workflow.version},
            idempotency_key=KEY_PREFIX + key,
        )
        session.commit()
    return ctx.tenant_id


async def request_commit(
    sessions: sessionmaker[Session],
    storage: StorageBackend,
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    if_match: str | None,
    key: str,
) -> CommitAccepted:
    manifest_hash = hash_of_if_match(if_match)
    correlation_id = correlation_id_var.get() or str(uuid7())
    tenant_id = await asyncio.to_thread(
        _approve,
        sessions,
        storage,
        user,
        project_id,
        workflow_id,
        manifest_hash,
        key,
        correlation_id,
    )
    spec = StartSpec(
        workflow=WORKFLOW_COMMIT,
        workflow_id=workflow_commit_id(workflow_id),
        queue=TaskQueue.CERTIFY,
        subject_type=SubjectType.PROJECT,
        subject_id=project_id,
        stage="recheck",
        input=WorkflowCommitInput(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=uuid7(),
            correlation_id=correlation_id,
            workflow_id=workflow_id,
            manifest_hash=manifest_hash,
            actor_id=str(user.user_id),
        ),
    )
    try:
        client = await get_client()
        run: Run = await start_run(client, sessions, spec)
    except OrchestratorUnavailable:
        raise TemporalUnavailable() from None
    return CommitAccepted(run=run, manifest_hash=manifest_hash)


# --- the steps of the WorkflowCommit activities ------------------------------------------


def recheck(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    manifest_hash: str,
    storage: StorageBackend,
) -> Evaluation:
    """The same checks again, in the worker, on the hash the FDE approved (a bundle that no longer
    matches its recorded digest is refused here too)."""
    evaluation = checks.evaluate(session, ctx, workflow_id, storage)
    if not evaluation.approved(manifest_hash):
        raise stale()
    if not evaluation.ready:
        raise CommitRefused(evaluation.reasons())
    return evaluation


def staging_request(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, actor_id: str
) -> StagingStartRequest:
    """What the runtime is asked to start: the STAGING entry that was registered, secret names."""
    entry = RegisteredWorkflowRepository(session, ctx).current(workflow_id, RegistryKind.STAGING)
    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if entry is None or workflow is None:
        raise not_found()
    names = sorted(
        c.name for c in connections.expected_for_workflow(session, ctx, workflow_id).names
    )
    bundle = None if not entry.ui_bundle else str(entry.ui_bundle["digest"])
    return StagingStartRequest(
        tenant_id=ctx.tenant_id,
        project_id=workflow.project_id,
        workflow_id=workflow_id,
        version=entry.version,
        registry_name=entry.registry_name,
        image_digest=entry.image_digest,
        manifest_hash=entry.manifest_hash,
        ui_bundle_digest=bundle,
        secret_names=names,
        requested_by=actor_id,
    )


def record_committed(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    inp: WorkflowCommitInput,
    registry_name: str,
    image_digest: str,
) -> None:
    """The audit row of who approved what. Written once per manifest hash."""
    key = f"{COMMITTED}:{inp.manifest_hash}"
    if find_by_key(session, ctx.tenant_id, ENTITY, str(workflow_id), key) is not None:
        return
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=inp.actor_id,
        action=COMMITTED,
        entity_type=ENTITY,
        entity_id=str(workflow_id),
        correlation_id=inp.correlation_id,
        after={
            "manifest_hash": inp.manifest_hash,
            "registry_name": registry_name,
            "image_digest": image_digest,
            "kind": RegistryKind.STAGING.value,
        },
        idempotency_key=key,
    )


def record_staging(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    inp: WorkflowCommitInput,
    result: StagingStartResult,
) -> None:
    """What the Staging runtime answered. A new answer for the same hash is a new row (a re-run
    after PENDING_RUNTIME or FAILED); the newest one is shown."""
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=WORKER,
        action=STAGING,
        entity_type=ENTITY,
        entity_id=str(workflow_id),
        correlation_id=inp.correlation_id,
        after={
            "manifest_hash": inp.manifest_hash,
            "status": result.status.value,
            "detail": result.detail,
        },
    )
