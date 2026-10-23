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

The signal goes last, and the transaction commits only once Temporal took it: a pipeline that is
not waiting (409 PIPELINE_NOT_WAITING) or a Temporal that is down (503 ORCHESTRATOR_UNAVAILABLE)
leaves nothing approved. Approving the same version with the same hash again answers 200 and
sends nothing, which also makes the loser of two concurrent approvals a 200.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sbam_contracts.api import ErrorCode
from sbam_contracts.pipeline import PipelineStage, SowApproved
from sbam_contracts.sow_api import SowApproval, SowApproveRequest, SowVersionStatus
from sbam_contracts.state_machines import SOW_VERSION, Actor, SowVersionState
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.base import utcnow
from sbam_api.pipeline.service import signal_pipeline
from sbam_api.sow.models import SowVersion
from sbam_api.sow.repository import SowRepository, SowVersionRepository
from sbam_api.sow.service import canonical_sha256
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID

SOW_APPROVED = "SOW_APPROVED"  # audit action
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


def _conflict(code: ErrorCode, title: str, detail: str) -> ApiError:
    return ApiError(409, code, title, detail)


@dataclass(frozen=True)
class Prepared:
    approval: SowApproval
    version_id: uuid.UUID
    sha256: str
    signal: bool  # False: already approved with this hash, nothing to send


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
        return Prepared(_approval(row), row.id, sha256, signal=False)
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

    if sow.approved_version_id and sow.approved_version_id != row.id:
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
    return Prepared(_approval(row), row.id, sha256, signal=True)


async def approve_version(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    number: int,
    body: SowApproveRequest,
) -> SowApproval:
    if (user.username or "").startswith(SERVICE_ACCOUNT_PREFIX):
        raise ApiError(
            403,
            ErrorCode.WRONG_PORTAL,
            "Approval needs a person",
            "A SOW is approved by an FDE signed in interactively, never by a service account.",
        )
    correlation_id = correlation_id_var.get() or str(uuid7())
    with sessions() as session:
        try:
            prepared = await asyncio.to_thread(
                _prepare, session, user, project_id, number, body.sha256, correlation_id
            )
            if prepared.signal:
                client = await get_client()
                await signal_pipeline(
                    client,
                    project_id,
                    "sow_approved",
                    SowApproved(
                        sow_version_id=prepared.version_id,
                        sha256=prepared.sha256,
                        actor_id=str(user.user_id),
                    ),
                    expect_stage=PipelineStage.WAIT_SOW_APPROVAL,
                )
            await asyncio.to_thread(session.commit)
            return prepared.approval
        except BaseException:
            await asyncio.to_thread(session.rollback)
            raise
