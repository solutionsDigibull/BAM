"""E1.8 state-machine service on SQLite: transitions, errors, audit rows, replay, outbox."""

import uuid
from typing import Any

import pytest
from sbam_api.audit import AuditLogSecurityAudit, dispatch_pending
from sbam_api.audit.outbox import OutboxItem
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.state_machine import (
    SYSTEM_ACTOR_ID,
    TransitionCommand,
    apply_create,
    apply_transition,
    transition,
)
from sbam_api.state_machine import service as service_module
from sbam_contracts.state_machines import Actor, MachineName
from sqlalchemy.orm import Session, sessionmaker
from state_db import SQLITE_MODELS, add, audit_rows, current, outbox_rows, sqlite_sessions

DOC, SOW, WORKFLOW, SUB_SKILL = (
    SQLITE_MODELS[m]
    for m in (
        MachineName.DOCUMENT_VERSION,
        MachineName.SOW_VERSION,
        MachineName.WORKFLOW,
        MachineName.SUB_SKILL,
    )
)
TENANT_A, TENANT_B, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CTX = TenantContext(TENANT_A, PROJECT)
FDE_ID = str(uuid.uuid4())


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    return sqlite_sessions()


def cmd(
    model: type[Any], entity_id: uuid.UUID, event: str, actor: Actor, **kw: Any
) -> TransitionCommand:
    actor_id = SYSTEM_ACTOR_ID if actor is Actor.SYSTEM else FDE_ID
    return TransitionCommand(
        model=model,
        entity_id=entity_id,
        event=event,
        actor=actor,
        actor_id=actor_id,
        correlation_id="cid-e1-8",
        **kw,
    )


def run(sessions: sessionmaker[Session], c: TransitionCommand, ctx: TenantContext = CTX) -> Any:
    return transition(sessions, ctx, c)


def error_of(
    sessions: sessionmaker[Session], c: TransitionCommand, ctx: TenantContext = CTX
) -> ApiError:
    with pytest.raises(ApiError) as err:
        run(sessions, c, ctx)
    return err.value


def test_e1_8_t01_valid_transition_bumps_version_and_writes_one_audit_row(
    sessions: sessionmaker[Session],
) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT_A, PROJECT)
    result = run(sessions, cmd(DOC, doc, "scan_started", Actor.SYSTEM))
    assert (result.from_state, result.to_state, result.row_version) == ("UPLOADED", "SCANNING", 2)
    assert current(sessions, DOC, doc) == ("SCANNING", 2)
    [audit] = audit_rows(sessions, doc)
    assert (audit.action, audit.actor_id, audit.entity_type) == (
        "scan_started",
        "SYSTEM",
        "document_version",
    )
    assert audit.before == {"state": "UPLOADED", "row_version": 1}
    assert audit.after == {"state": "SCANNING", "row_version": 2}
    assert audit.correlation_id == "cid-e1-8" and audit.tenant_id == TENANT_A
    [note] = outbox_rows(sessions)
    assert note.type == "document_version.scan_started" and note.dispatched_at is not None


def test_e1_8_t02_not_allowed_is_409_and_writes_nothing(sessions: sessionmaker[Session]) -> None:
    sow = add(sessions, SOW, "GENERATED", TENANT_A, PROJECT)
    err = error_of(sessions, cmd(SOW, sow, "approve", Actor.FDE))  # must open review first
    assert (err.status, err.code) == (409, "INVALID_TRANSITION")
    assert current(sessions, SOW, sow) == ("GENERATED", 1)
    assert audit_rows(sessions) == [] and outbox_rows(sessions) == []


def test_e1_8_t02_issue_scenario_edit_on_approved_sow_is_locked_423(
    sessions: sessionmaker[Session],
) -> None:
    """The issue's T02 scenario: APPROVED is a locked SOW state, so edit is 423 (TD §14.8)."""
    sow = add(sessions, SOW, "APPROVED", TENANT_A, PROJECT)
    err = error_of(sessions, cmd(SOW, sow, "edit", Actor.FDE))
    assert (err.status, err.code) == (423, "RESOURCE_LOCKED")
    assert audit_rows(sessions) == []


def test_e1_8_t03_stale_row_version_is_412_and_state_unchanged(
    sessions: sessionmaker[Session],
) -> None:
    sow = add(sessions, SOW, "IN_REVIEW", TENANT_A, PROJECT)
    run(sessions, cmd(SOW, sow, "edit", Actor.FDE))  # row_version 2
    run(sessions, cmd(SOW, sow, "edit", Actor.FDE))  # row_version 3
    err = error_of(sessions, cmd(SOW, sow, "approve", Actor.FDE, expected_row_version=2))
    assert (err.status, err.code) == (412, "PRECONDITION_FAILED")
    assert current(sessions, SOW, sow) == ("IN_REVIEW", 3)
    assert len(audit_rows(sessions, sow)) == 2


def test_e1_8_self_loop_edit_still_bumps_row_version(sessions: sessionmaker[Session]) -> None:
    sow = add(sessions, SOW, "IN_REVIEW", TENANT_A, PROJECT)
    result = run(sessions, cmd(SOW, sow, "edit", Actor.FDE, expected_row_version=1))
    assert (result.to_state, result.row_version) == ("IN_REVIEW", 2)


def test_e1_8_t05_audit_failure_rolls_back_the_transition(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT_A, PROJECT)

    def broken_record(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(service_module, "record", broken_record)
    with pytest.raises(RuntimeError, match="audit insert failed"):
        run(sessions, cmd(DOC, doc, "scan_started", Actor.SYSTEM))
    assert current(sessions, DOC, doc) == ("UPLOADED", 1)
    assert audit_rows(sessions) == [] and outbox_rows(sessions) == []


def test_e1_8_t06_fde_cannot_create_a_workflow(sessions: sessionmaker[Session]) -> None:
    """Only SYSTEM creates a Workflow (sow_approved); the denied attempt is a security event."""
    with sessions() as session:
        with pytest.raises(ApiError) as err:
            apply_create(
                session,
                CTX,
                WORKFLOW(),
                event="sow_approved",
                actor=Actor.FDE,
                actor_id=FDE_ID,
                correlation_id="cid-t06",
            )
        session.rollback()
    assert (err.value.status, err.value.code) == (409, "INVALID_TRANSITION")
    assert "SYSTEM event" in (err.value.detail or "")
    [denied] = audit_rows(sessions)
    assert (denied.action, denied.actor_id, denied.entity_type) == (
        "transition_denied",
        FDE_ID,
        "workflow",
    )
    assert denied.after == {"event": "sow_approved", "actor": "FDE", "reason": "FORBIDDEN_ACTOR"}
    with sessions() as session:
        assert session.query(WORKFLOW).count() == 0


def test_e1_8_system_creates_workflow_on_sow_approved(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        result = apply_create(
            session,
            CTX,
            WORKFLOW(),
            event="sow_approved",
            actor=Actor.SYSTEM,
            actor_id=SYSTEM_ACTOR_ID,
            correlation_id="cid-create",
        )
        session.commit()
    assert (result.from_state, result.to_state, result.row_version) == (None, "CREATED", 1)
    [audit] = audit_rows(sessions, result.entity_id)
    assert audit.before is None and audit.after == {"state": "CREATED", "row_version": 1}


def test_e1_8_forbidden_actor_on_existing_entity(sessions: sessionmaker[Session]) -> None:
    wf = add(sessions, WORKFLOW, "CREATED", TENANT_A, PROJECT)
    err = error_of(sessions, cmd(WORKFLOW, wf, "start_extraction", Actor.FDE))  # a SYSTEM event
    assert (err.status, err.code) == (409, "INVALID_TRANSITION")
    assert [r.action for r in audit_rows(sessions, wf)] == ["transition_denied"]
    assert current(sessions, WORKFLOW, wf) == ("CREATED", 1)


def test_e1_8_t07_replay_with_same_key_is_a_no_op(sessions: sessionmaker[Session]) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT_A, PROJECT)
    key = "run-123:scan"
    first = run(sessions, cmd(DOC, doc, "scan_started", Actor.SYSTEM, idempotency_key=key))
    again = run(sessions, cmd(DOC, doc, "scan_started", Actor.SYSTEM, idempotency_key=key))
    assert not first.replayed and again.replayed
    assert (again.to_state, again.row_version, again.audit_id) == ("SCANNING", 2, first.audit_id)
    assert current(sessions, DOC, doc) == ("SCANNING", 2)
    assert len(audit_rows(sessions, doc)) == 1 and len(outbox_rows(sessions)) == 1


def test_e1_8_t07_without_key_a_replay_is_rejected(sessions: sessionmaker[Session]) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT_A, PROJECT)
    run(sessions, cmd(DOC, doc, "scan_started", Actor.SYSTEM))
    err = error_of(sessions, cmd(DOC, doc, "scan_started", Actor.SYSTEM))
    assert err.status == 409  # SCANNING has no scan_started edge


def test_e1_8_t08_edit_on_frozen_sub_skill_is_423(sessions: sessionmaker[Session]) -> None:
    sub = add(sessions, SUB_SKILL, "FROZEN", TENANT_A, PROJECT)
    err = error_of(sessions, cmd(SUB_SKILL, sub, "edit", Actor.FDE))
    assert (err.status, err.code) == (423, "RESOURCE_LOCKED")
    assert audit_rows(sessions) == []


@pytest.mark.parametrize(
    "ctx",
    [TenantContext(TENANT_B, PROJECT), TenantContext(TENANT_A, uuid.uuid4())],
    ids=["other-tenant", "other-project"],
)
def test_e1_8_t09_cross_tenant_or_project_is_404_and_nothing_written(
    sessions: sessionmaker[Session], ctx: TenantContext
) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT_A, PROJECT)
    err = error_of(sessions, cmd(DOC, doc, "scan_started", Actor.SYSTEM), ctx)
    assert (err.status, err.code) == (404, "NOT_FOUND")
    assert current(sessions, DOC, doc) == ("UPLOADED", 1)
    assert audit_rows(sessions) == []


def test_e1_8_correlation_id_is_required(sessions: sessionmaker[Session]) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT_A, PROJECT)
    bad = TransitionCommand(
        DOC, doc, "scan_started", Actor.SYSTEM, SYSTEM_ACTOR_ID, correlation_id=""
    )
    with pytest.raises(ValueError, match="correlation_id"):
        run(sessions, bad)


def test_e1_8_notifications_only_after_commit_and_failures_retry(
    sessions: sessionmaker[Session],
) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT_A, PROJECT)
    with sessions() as session:  # applied but rolled back: nothing to notify
        apply_transition(session, CTX, cmd(DOC, doc, "scan_started", Actor.SYSTEM))
        session.rollback()
    assert outbox_rows(sessions) == []

    sent: list[OutboxItem] = []

    class Broken:
        def send(self, item: OutboxItem) -> None:
            raise ConnectionError("notifications down")

    class Recording:
        def send(self, item: OutboxItem) -> None:
            sent.append(item)

    result = transition(sessions, CTX, cmd(DOC, doc, "scan_started", Actor.SYSTEM), sink=Broken())
    assert result.to_state == "SCANNING"  # the transition stands
    [pending] = outbox_rows(sessions)
    assert (pending.dispatched_at, pending.attempts, pending.last_error) == (
        None,
        1,
        "ConnectionError",
    )
    assert dispatch_pending(sessions, Recording()) == 1
    assert [s.type for s in sent] == ["document_version.scan_started"]
    assert outbox_rows(sessions)[0].dispatched_at is not None
    assert dispatch_pending(sessions, Recording()) == 0


def test_e1_8_security_audit_port_writes_access_denied(sessions: sessionmaker[Session]) -> None:
    user = CurrentUser(
        user_id=uuid.uuid4(), keycloak_sub="sub", tenant_id=TENANT_A, roles=frozenset({"sbam-fde"})
    )
    AuditLogSecurityAudit(lambda: sessions).denied(
        actor=user, route="/api/v1/projects/x", target="p-1", reason="not assigned"
    )
    [row] = audit_rows(sessions)
    assert (row.action, row.actor_id, row.entity_id) == ("access_denied", str(user.user_id), "p-1")
    assert row.after == {"route": "/api/v1/projects/x", "reason": "not assigned"}

    def no_db() -> sessionmaker[Session]:
        raise ConnectionError("db down")

    AuditLogSecurityAudit(no_db).denied(actor=user, route="/r", target=None, reason="x")  # no raise
