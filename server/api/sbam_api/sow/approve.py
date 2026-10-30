"""POST P/sow/versions/{v}/approve (E7.1, TD §8.7, §14.3): the FDE approves the SOW they reviewed.

The approval binds the version's sha256, the FDE and the time, locks the version, and signals the
waiting ProjectPipeline (`sow_approved`), which then creates the Workflow. Under the `sows` row
lock, in one transaction:

* only a person approves: an FDE token, never a service account (403);
* the version must be the latest (409 SOW_NOT_LATEST), not a failed or superseded one (409), and
  the request's sha256, the stored one and a fresh hash of its content must agree (409 SOW_STALE);
* the version becomes APPROVED (and locked: its content was already immutable, and a save onto it
  is 423), a previously approved version becomes SUPERSEDED, `sows.approved_version_id` moves, and
  audit_log gets SOW_APPROVED with the version and hash. The state machine writes the notification.
* a scope change (another version was approved before, E7.2): 409 WORKFLOW_BUILDING while that
  version's Workflow builds or certifies; otherwise its impact is computed by the preview's own
  function and stored (`sow_impacts`), so what the FDE previewed is what happens. The pipeline is
  not waiting for an approval any more, so instead of the signal a new run replaces it, resuming
  at the approval with this one (`restart_pipeline`: 409 PIPELINE_RUNNING while the old run is
  still working); it creates the next MAJOR Workflow and carries the untouched rows over.

The approval commits first and the pipeline is told after (A-03). Telling it first and committing
after meant `create_workflow`, which treats an unapproved version as non-retryable, could read the
row before the commit and fail a legitimate approval, and the `sows` lock was held over a Temporal
call. Now: a read-only look (no lock) checks the pipeline can take the approval (409
PIPELINE_NOT_WAITING / PIPELINE_RUNNING, 503 ORCHESTRATOR_UNAVAILABLE: nothing is approved), the
approval commits, then the signal (or the run that replaces the pipeline) goes out and
`signal_sent_at` records that it was taken. If Temporal fails in that gap the approval stays saved
with `signal_sent_at` NULL: the caller gets the 409/503, approving again with the same hash sends it
again, and `redeliver_pending` (a worker schedule) sends it for anyone who does not come back. A
version whose Workflow already exists, or whose `signal_sent_at` is set, is never sent twice.
Approving the same version with the same hash again answers 200, which also makes the loser of two
concurrent approvals a 200.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sbam_contracts.api import ErrorCode
from sbam_contracts.pipeline import PipelineInput, PipelineStage, ResumeState, SowApproved
from sbam_contracts.sow_api import SowApproval, SowApproveRequest, SowVersionStatus
from sbam_contracts.state_machines import SOW_VERSION, Actor, SowVersionState, WorkflowState
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.pipeline.service import (
    RESTARTABLE,
    PipelineBusy,
    PipelineNotWaiting,
    pipeline_status,
    restart_pipeline,
    signal_pipeline,
)
from sbam_api.sow.impact import record_impact
from sbam_api.sow.models import SowVersion
from sbam_api.sow.repository import (
    SowImpactRepository,
    SowRepository,
    SowVersionRepository,
    pending_approvals,
)
from sbam_api.sow.service import canonical_sha256
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID

SOW_APPROVED = "SOW_APPROVED"  # audit action
CLAIM_STALE = timedelta(seconds=30)  # a delivery claim older than this belongs to a dead process
# A scope change can't be approved while the current Workflow is being built (E7.2).
BUILDING = frozenset({WorkflowState.BUILDING.value, WorkflowState.CERTIFYING.value})
SERVICE_ACCOUNT_PREFIX = "service-account-"  # Keycloak's username for a client's own token


def _utc(when: datetime | None) -> datetime:
    """Aware UTC: Postgres returns it so; SQLite (tests) drops the zone."""
    when = when or utcnow()
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _approval(row: SowVersion) -> SowApproval:
    return SowApproval(
        version=row.version,
        status=SowVersionStatus(row.state),
        is_locked=SowVersionState(row.state) in SOW_VERSION.locked,
        approved_at=_utc(row.approved_at),
        approved_by=row.approved_by or "",
        sha256=row.sha256 or "",
    )


def _tenant(user: CurrentUser) -> uuid.UUID:
    if user.tenant_id is None:  # an FDE always has one (the `fde` policy)
        raise not_found()
    return user.tenant_id


def _conflict(code: ErrorCode, title: str, detail: str) -> ApiError:
    return ApiError(409, code, title, detail)


@dataclass(frozen=True)
class Prepared:
    approval: SowApproval
    version_id: uuid.UUID
    sha256: str
    signal: bool  # False: already approved with this hash, nothing to send
    scope_change: bool = False  # another version was approved before (E7.2)
    redelivery: bool = False  # approved earlier; only the pipeline is still to be told (A-03)


def _prepare(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    number: int,
    sha256: str,
    correlation_id: str,
) -> Prepared:
    """Checks and the approval itself, in the caller's open transaction (not committed here)."""
    ctx = user.context(project_id)
    sows = SowRepository(session, ctx)
    sow = sows.of_project(for_update=True)
    if sow is None:
        raise not_found()
    versions = SowVersionRepository(session, ctx)
    row = versions.by_number(sow.id, number)
    if row is None:
        raise not_found()
    state = SowVersionState(row.state)
    if state is SowVersionState.APPROVED and row.sha256 == sha256:
        pending = row.signal_sent_at is None  # saved, the pipeline not told yet (A-03)
        again = SowImpactRepository(session, ctx).of_version(row.id) is not None
        return Prepared(
            _approval(row), row.id, sha256, signal=pending, scope_change=again, redelivery=True
        )
    if state not in (SowVersionState.GENERATED, SowVersionState.IN_REVIEW):
        raise _conflict(
            ErrorCode.INVALID_TRANSITION,
            "This version cannot be approved",
            f"A {state.value} version is not approved; approve the latest version under review.",
        )
    latest = max(v.version for v in versions.of_sow(sow.id))
    if row.version != latest:
        raise _conflict(
            ErrorCode.SOW_NOT_LATEST,
            "Not the latest version",
            f"v{row.version} is not the latest; v{latest} is. Review and approve v{latest}.",
        )
    if (
        row.structured is None
        or sha256 != row.sha256
        or canonical_sha256(row.structured) != row.sha256
    ):
        raise _conflict(
            ErrorCode.SOW_STALE,
            "The SOW changed",
            "The version you reviewed is not the one stored; reload it before approving.",
        )

    scope_change = False
    if sow.approved_version_id and sow.approved_version_id != row.id:
        base = versions.get(sow.approved_version_id)
        if base is not None:
            # Here, not at the top: the Workflow model imports the SOW package.
            from sbam_api.workflows.repository import WorkflowRepository

            workflow = WorkflowRepository(session, ctx).by_sow_version(base.id)
            if workflow is not None and workflow.state in BUILDING:
                raise _conflict(
                    ErrorCode.WORKFLOW_BUILDING,
                    "A build is running",
                    f"Workflow {workflow.version} is being built; approve the new version once "
                    "it has finished.",
                )
            record_impact(session, ctx, base, row)
        scope_change = True
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=SowVersion,
                entity_id=sow.approved_version_id,
                event="superseded",
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=correlation_id,
            ),
        )
    apply_transition(
        session,
        ctx,
        TransitionCommand(
            model=SowVersion,
            entity_id=row.id,
            event="approve",
            actor=Actor.FDE,
            actor_id=str(user.user_id),
            correlation_id=correlation_id,
        ),
    )
    row.approved_at = utcnow()
    row.approved_by = str(user.user_id)
    row.signal_claimed_at = row.approved_at  # the approver's request tells the pipeline (A-03)
    versions.update(row, row.row_version)
    sow.approved_version_id = row.id
    sows.update(sow, sow.row_version)
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=SOW_APPROVED,
        entity_type="sow_version",
        entity_id=str(row.id),
        correlation_id=correlation_id,
        after={
            "version": row.version,
            "sha256": sha256,
            "approved_by": row.approved_by,
            "approved_at": row.approved_at.isoformat(),
        },
    )
    session.flush()
    return Prepared(_approval(row), row.id, sha256, signal=True, scope_change=scope_change)


async def _precheck(
    get_client: Callable[[], Awaitable[Client]], project_id: uuid.UUID, scope_change: bool
) -> None:
    """Before anything is approved: can the pipeline take it? Read-only, no lock held."""
    status = await pipeline_status(await get_client(), project_id)
    if scope_change:
        if status is not None and status.stage not in RESTARTABLE:
            raise PipelineBusy(status.stage)
    elif status is None or status.stage is not PipelineStage.WAIT_SOW_APPROVAL:
        raise PipelineNotWaiting(PipelineStage.WAIT_SOW_APPROVAL)


def _would_be_scope_change(
    session: Session, user: CurrentUser, project_id: uuid.UUID, number: int
) -> bool | None:
    """Whether approving this version replaces an earlier approval (None: nothing to look at, let
    `_prepare` give the real answer). A plain read; the real check runs under the lock."""
    ctx = user.context(project_id)
    sow = SowRepository(session, ctx).of_project()
    if sow is None:
        return None
    row = SowVersionRepository(session, ctx).by_number(sow.id, number)
    if row is None or row.state not in (
        SowVersionState.GENERATED.value,
        SowVersionState.IN_REVIEW.value,
    ):
        return None
    return bool(sow.approved_version_id and sow.approved_version_id != row.id)


def _workflow_exists(session: Session, ctx: TenantContext, version_id: uuid.UUID) -> bool:
    from sbam_api.workflows.repository import WorkflowRepository

    return WorkflowRepository(session, ctx).by_sow_version(version_id) is not None


def _claim(sessions: sessionmaker[Session], ctx: TenantContext, version_id: uuid.UUID) -> bool:
    with sessions() as session:
        won = SowVersionRepository(session, ctx).claim_delivery(version_id, utcnow(), CLAIM_STALE)
        session.commit()
        return won


def _release(sessions: sessionmaker[Session], ctx: TenantContext, version_id: uuid.UUID) -> None:
    with sessions() as session:
        SowVersionRepository(session, ctx).release_delivery(version_id)
        session.commit()


def _mark_sent(sessions: sessionmaker[Session], ctx: TenantContext, version_id: uuid.UUID) -> None:
    with sessions() as session:
        row = SowVersionRepository(session, ctx).get(version_id)
        if row is not None and row.signal_sent_at is None:
            row.signal_sent_at = utcnow()
            session.commit()


async def deliver_approval(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    *,
    ctx: TenantContext,
    version_id: uuid.UUID,
    sha256: str,
    actor_id: str,
    scope_change: bool,
    correlation_id: str,
    redelivery: bool,
    with_search_attributes: bool = True,
) -> bool:
    """Tell the pipeline about a committed approval, then record that it was told. True when this
    call told it (False: another caller holds the claim or already did). A failure leaves
    `signal_sent_at` NULL and is raised (409/503): the approval is saved and can be sent again.

    On a redelivery, a pipeline that is no longer waiting (or busy) because it already took the
    approval counts as delivered: that is when the Workflow for this version exists."""
    if ctx.project_id is None:
        raise not_found()
    project_id = ctx.project_id
    if redelivery and not await asyncio.to_thread(_claim, sessions, ctx, version_id):
        return False  # someone else is telling the pipeline right now (or just did): never twice
    approved = SowApproved(sow_version_id=version_id, sha256=sha256, actor_id=actor_id)
    try:
        client = await get_client()
        if scope_change:
            await restart_pipeline(
                client,
                sessions,
                PipelineInput(
                    tenant_id=ctx.tenant_id,
                    project_id=project_id,
                    run_id=uuid7(),
                    correlation_id=correlation_id,
                    actor_id=actor_id,
                    search_attributes=with_search_attributes,
                    resume=ResumeState(
                        stage=PipelineStage.WAIT_SOW_APPROVAL, sow_approved=approved
                    ),
                ),
                with_search_attributes=with_search_attributes,
            )
        else:
            await signal_pipeline(
                client,
                project_id,
                "sow_approved",
                approved,
                expect_stage=PipelineStage.WAIT_SOW_APPROVAL,
            )
    except (PipelineNotWaiting, PipelineBusy):
        with sessions() as session:
            taken = redelivery and _workflow_exists(session, ctx, version_id)
        if not taken:
            await asyncio.to_thread(_release, sessions, ctx, version_id)
            raise
    except BaseException:
        await asyncio.to_thread(_release, sessions, ctx, version_id)  # a retry may send at once
        raise
    await asyncio.to_thread(_mark_sent, sessions, ctx, version_id)
    return True


async def approve_version(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    number: int,
    body: SowApproveRequest,
    *,
    with_search_attributes: bool = True,
) -> SowApproval:
    if (user.username or "").startswith(SERVICE_ACCOUNT_PREFIX):
        raise ApiError(
            403,
            ErrorCode.WRONG_PORTAL,
            "Approval needs a person",
            "A SOW is approved by an FDE signed in interactively, never by a service account.",
        )
    correlation_id = correlation_id_var.get() or str(uuid7())
    with sessions() as look:  # read-only, no lock: can the pipeline take an approval at all?
        replaces = await asyncio.to_thread(_would_be_scope_change, look, user, project_id, number)
    if replaces is not None:
        await _precheck(get_client, project_id, replaces)
    with sessions() as session:
        try:
            prepared = await asyncio.to_thread(
                _prepare, session, user, project_id, number, body.sha256, correlation_id
            )
            await asyncio.to_thread(session.commit)  # the lock is gone before Temporal is called
        except BaseException:
            await asyncio.to_thread(session.rollback)
            raise
    if prepared.signal:
        await deliver_approval(
            sessions,
            get_client,
            ctx=user.context(project_id),
            version_id=prepared.version_id,
            sha256=prepared.sha256,
            actor_id=str(user.user_id),
            scope_change=prepared.scope_change,
            correlation_id=correlation_id,
            redelivery=prepared.redelivery,
            with_search_attributes=with_search_attributes,
        )
    return prepared.approval


# --- the relay for approvals nobody came back to resend (A-03) ----------------------------------

RELAY_MIN_AGE = timedelta(seconds=60)  # younger ones are still being delivered by their request


async def redeliver_pending(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    now: datetime,
) -> tuple[int, int]:
    """Tell the pipelines of approvals that were saved but not delivered. Returns (sent, failed).
    One failing project never stops the others; the next run tries again."""
    with sessions() as session:
        rows = [
            (r.tenant_id, r.project_id, r.id, r.sha256 or "", r.approved_by or "")
            for r in pending_approvals(session, now - RELAY_MIN_AGE)
        ]
    sent = failed = 0
    for tenant_id, project_id, version_id, sha256, actor in rows:
        ctx = TenantContext(tenant_id, project_id)
        with sessions() as session:
            again = SowImpactRepository(session, ctx).of_version(version_id) is not None
        try:
            told = await deliver_approval(
                sessions,
                get_client,
                ctx=ctx,
                version_id=version_id,
                sha256=sha256,
                actor_id=actor,
                scope_change=again,
                correlation_id=f"sow-approval-relay-{version_id}",
                redelivery=True,
            )
            sent += 1 if told else 0
        except ApiError:
            failed += 1
    return sent, failed
