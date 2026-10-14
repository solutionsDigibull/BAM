"""Pipeline state activities (E10.1): state transitions and notifications, on every queue.

`apply_pipeline_transition` runs one transition through the state-machine service (#41): the row
lock, can_transition, the audit row and the outbox notification all happen in one DB transaction.
Its idempotency key ("<run>:<step>") makes an activity retry a no-op. If no table is registered
for the machine yet (projects #54, workflows #91), it does nothing and says so.

`notify` writes a notification that isn't a transition (reminder, failure with next action),
also through the append-only audit log + outbox, and also idempotent per key.
"""

import logging

from sbam_api.audit.outbox import LogSink, dispatch_pending
from sbam_api.audit.repository import enqueue, find_by_key, record
from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.state_machine import TransitionCommand, apply_create, apply_transition
from sbam_api.state_machine.registry import entity_model
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_contracts.pipeline import NotifyRequest, TransitionOutcome, TransitionRequest
from sbam_contracts.runs import TaskQueue
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.mirror import sessions
from sbam_workers.registry import activity_for

log = logging.getLogger("sbam_workers.pipeline_state")


@activity_for(*TaskQueue)
@activity.defn(name="apply_pipeline_transition")
def apply_pipeline_transition(req: TransitionRequest) -> TransitionOutcome:
    model = entity_model(req.machine)
    if model is None:  # the machine's table doesn't exist yet
        return TransitionOutcome(applied=False)
    ctx = TenantContext(req.tenant_id, req.project_id)
    with sessions()() as session:
        try:
            if req.create:
                result = apply_create(
                    session,
                    ctx,
                    model(id=req.entity_id),
                    event=req.event,
                    actor=req.actor,
                    actor_id=req.actor_id,
                    correlation_id=req.correlation_id,
                    idempotency_key=req.idempotency_key,
                )
            else:
                result = apply_transition(
                    session,
                    ctx,
                    TransitionCommand(
                        model=model,
                        entity_id=req.entity_id,
                        event=req.event,
                        actor=req.actor,
                        actor_id=req.actor_id,
                        correlation_id=req.correlation_id,
                        idempotency_key=req.idempotency_key,
                    ),
                )
            session.commit()
        except ApiError as exc:  # invalid transition / missing entity: retrying won't help
            session.rollback()
            raise ApplicationError(
                f"{req.machine.value}.{req.event} rejected: {exc.code}",
                type="TransitionRejected",
                non_retryable=True,
            ) from None
    _dispatch()
    return TransitionOutcome(applied=not result.replayed, to_state=result.to_state)


@activity_for(*TaskQueue)
@activity.defn(name="notify")
def notify(req: NotifyRequest) -> None:
    ctx_entity = str(req.entity_id)
    with sessions()() as session:
        if find_by_key(session, req.tenant_id, req.entity_type, ctx_entity, req.idempotency_key):
            return  # already notified (activity retry)
        audit = record(
            session,
            tenant_id=req.tenant_id,
            actor_id=SYSTEM_ACTOR_ID,
            action=req.type,
            entity_type=req.entity_type,
            entity_id=ctx_entity,
            after=dict(req.payload),
            correlation_id=req.correlation_id,
            idempotency_key=req.idempotency_key,
        )
        enqueue(
            session,
            tenant_id=req.tenant_id,
            audit_id=audit.id,
            type=req.type,
            payload={"project_id": str(req.project_id), "entity_id": ctx_entity, **req.payload},
        )
        session.commit()
    _dispatch()


def _dispatch() -> None:
    """After commit only; a failed dispatch leaves the row pending for the next pass."""
    try:
        dispatch_pending(sessions(), LogSink())
    except Exception as exc:
        log.warning("notification dispatch failed", extra={"exc_type": type(exc).__name__})
