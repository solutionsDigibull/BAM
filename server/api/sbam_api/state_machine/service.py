"""The state-machine service (TD §14.7, E1.8): the only way an entity's `state` changes.

`apply_transition` moves an existing entity; `apply_create` creates one through its creation event
(e.g. `sow_approved` creates a Workflow). Both run in the caller's DB transaction:
  1. load the row tenant-scoped with SELECT … FOR UPDATE (missing / other tenant -> 404)
  2. replay: the same `idempotency_key` already applied -> return that result, write nothing
  3. `expected_row_version` given and stale -> 412
  4. `can_transition` (E0.6 tables) -> 409 INVALID_TRANSITION, or 423 for edit on a locked state;
     a wrong actor (e.g. FDE + sow_approved) is also written to audit_log as a security event
  5. state change, row_version + 1 (a frozen row is refused by the DB trigger -> 423)
  6. one `audit_log` row (before/after) and one `notification_outbox` row
Nothing is committed here; any failure rolls all of it back with the caller's transaction.
`transition()` is the one-shot form: apply, commit, then dispatch notifications after the commit.

API handlers pass the caller as actor (Actor.FDE / Actor.ADMIN, actor_id = user id); Temporal
activities pass Actor.SYSTEM and an idempotency key "<temporal run id>:<step>", so a retry is a
no-op.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sbam_contracts.state_machines import (
    Actor,
    InvalidTransition,
    InvalidTransitionCode,
    can_transition,
)
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.audit.models import AuditLog
from sbam_api.audit.outbox import LogSink, NotificationSink, dispatch_pending
from sbam_api.audit.repository import enqueue, find_by_key, record
from sbam_api.core.conventions import not_found
from sbam_api.db.base import utcnow
from sbam_api.db.repository import StaleRowVersion, TenantContext
from sbam_api.state_machine.errors import transition_error
from sbam_api.state_machine.repository import repository_for

SYSTEM_ACTOR_ID = "SYSTEM"
log = logging.getLogger("sbam_api.state_machine")


@dataclass(frozen=True)
class TransitionCommand:
    model: type[Any]  # a `Stateful` entity class; its `machine` names the state machine
    entity_id: uuid.UUID
    event: str
    actor: Actor
    actor_id: str  # user id, or SYSTEM_ACTOR_ID for activities
    correlation_id: str
    expected_row_version: int | None = None  # If-Match from the API; activities leave it None
    idempotency_key: str | None = None  # activities: "<run id>:<step>"


@dataclass(frozen=True)
class TransitionResult:
    entity_id: uuid.UUID
    machine: str
    from_state: str | None  # None for a creation
    to_state: str
    row_version: int
    audit_id: uuid.UUID
    replayed: bool = False


def apply_transition(
    session: Session, ctx: TenantContext, cmd: TransitionCommand
) -> TransitionResult:
    _require_correlation(cmd.correlation_id)
    machine = cmd.model.machine.value
    repo = repository_for(cmd.model)(session, ctx)
    entity = repo.get_for_update(cmd.entity_id)
    if entity is None:
        raise not_found()
    if cmd.idempotency_key:
        replay = _replay(session, ctx, machine, cmd.entity_id, cmd.idempotency_key)
        if replay is not None:
            return replay
    if cmd.expected_row_version is not None and entity.row_version != cmd.expected_row_version:
        raise StaleRowVersion()

    from_state: str = entity.state
    to_state = _allowed(session, ctx, machine, cmd.entity_id, from_state, cmd)
    before = {"state": from_state, "row_version": entity.row_version}
    entity.state = to_state
    entity.updated_at = utcnow()  # forces the UPDATE (and row_version + 1) on self-loops too
    repo.update(entity, entity.row_version)  # flush: stale -> 412, frozen trigger -> 423
    return _finish(session, ctx, machine, entity, cmd, before)


def apply_create(
    session: Session,
    ctx: TenantContext,
    entity: Any,
    *,
    event: str,
    actor: Actor,
    actor_id: str,
    correlation_id: str,
    idempotency_key: str | None = None,
) -> TransitionResult:
    """Insert `entity` (a new, unsaved `Stateful` row) in the state its creation event leads to.

    With an idempotency key the entity needs its id set up front (a natural key), so a retry finds
    the row it created the first time.
    """
    _require_correlation(correlation_id)
    model = type(entity)
    machine = model.machine.value
    cmd = TransitionCommand(
        model=model,
        entity_id=entity.id or uuid.UUID(int=0),
        event=event,
        actor=actor,
        actor_id=actor_id,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    if idempotency_key and entity.id is not None:
        replay = _replay(session, ctx, machine, entity.id, idempotency_key)
        if replay is not None:
            return replay
    entity.state = _allowed(session, ctx, machine, entity.id, None, cmd)
    repository_for(model)(session, ctx).add(entity)  # stamps tenant/project, flushes (id set)
    return _finish(session, ctx, machine, entity, cmd, before=None)


def transition(
    sessions: sessionmaker[Session],
    ctx: TenantContext,
    cmd: TransitionCommand,
    sink: NotificationSink | None = None,
) -> TransitionResult:
    """Apply and commit one transition, then dispatch its notification (after commit only)."""
    with sessions() as session:
        result = apply_transition(session, ctx, cmd)
        session.commit()
    if not result.replayed:
        try:
            dispatch_pending(sessions, sink or LogSink())
        except Exception as exc:  # the row stays pending; the transition stands
            log.warning("notification dispatch failed", extra={"exc_type": type(exc).__name__})
    return result


# --- steps ---------------------------------------------------------------------------------------


def _require_correlation(correlation_id: str) -> None:
    if not correlation_id:
        raise ValueError("a transition needs a correlation_id")


def _replay(
    session: Session, ctx: TenantContext, machine: str, entity_id: uuid.UUID, key: str
) -> TransitionResult | None:
    prior = find_by_key(session, ctx.tenant_id, machine, str(entity_id), key)
    if prior is None or prior.after is None:
        return None
    return TransitionResult(
        entity_id=entity_id,
        machine=machine,
        from_state=prior.before["state"] if prior.before else None,
        to_state=prior.after["state"],
        row_version=prior.after["row_version"],
        audit_id=prior.id,
        replayed=True,
    )


def _allowed(
    session: Session,
    ctx: TenantContext,
    machine: str,
    entity_id: uuid.UUID | None,
    from_state: str | None,
    cmd: TransitionCommand,
) -> str:
    try:
        return can_transition(machine, from_state, cmd.event, cmd.actor)
    except InvalidTransition as exc:
        if exc.code is InvalidTransitionCode.FORBIDDEN_ACTOR:
            _record_denied(session, ctx, machine, entity_id, from_state, cmd, exc)
        raise transition_error(exc) from None


def _finish(
    session: Session,
    ctx: TenantContext,
    machine: str,
    entity: Any,
    cmd: TransitionCommand,
    before: dict[str, Any] | None,
) -> TransitionResult:
    after = {"state": entity.state, "row_version": entity.row_version}
    audit: AuditLog = record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=cmd.actor_id,
        action=cmd.event,
        entity_type=machine,
        entity_id=str(entity.id),
        before=before,
        after=after,
        correlation_id=cmd.correlation_id,
        idempotency_key=cmd.idempotency_key,
    )
    enqueue(
        session,
        tenant_id=ctx.tenant_id,
        audit_id=audit.id,
        type=f"{machine}.{cmd.event}",
        payload={
            "entity_id": str(entity.id),
            "project_id": str(ctx.project_id) if ctx.project_id else None,
            "from_state": before["state"] if before else None,
            "to_state": entity.state,
        },
    )
    return TransitionResult(
        entity_id=entity.id,
        machine=machine,
        from_state=before["state"] if before else None,
        to_state=entity.state,
        row_version=entity.row_version,
        audit_id=audit.id,
    )


def _record_denied(
    session: Session,
    ctx: TenantContext,
    machine: str,
    entity_id: uuid.UUID | None,
    from_state: str | None,
    cmd: TransitionCommand,
    exc: InvalidTransition,
) -> None:
    """Security event for a forbidden actor (E1.8-T06), committed on its own connection so it
    survives the caller's rollback."""
    with Session(session.get_bind()) as audit_session:
        record(
            audit_session,
            tenant_id=ctx.tenant_id,
            actor_id=cmd.actor_id,
            action="transition_denied",
            entity_type=machine,
            entity_id=str(entity_id) if entity_id else None,
            before={"state": from_state},
            after={"event": cmd.event, "actor": cmd.actor.value, "reason": exc.code.value},
            correlation_id=cmd.correlation_id,
        )
        audit_session.commit()
