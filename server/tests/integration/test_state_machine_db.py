"""E1.8 against the Compose Postgres: real transactions, row locks and triggers (TD §19.3).

A scratch database migrated to head (real `audit_log` / `notification_outbox` with the append-only
trigger) plus a scratch schema of stand-in entity tables; both are dropped at the end.
"""

import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sbam_api.core.conventions import ApiError
from sbam_api.db.frozen import create_frozen_function, create_frozen_trigger
from sbam_api.db.migrate import config
from sbam_api.db.repository import RowFrozen, TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition, transition
from sbam_api.state_machine import service as service_module
from sbam_contracts.state_machines import Actor, MachineName
from sqlalchemy import pool, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from state_db import add, audit_rows, current, entity_base, entity_models, outbox_rows

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e18_{uuid.uuid4().hex[:10]}"
SCRATCH = "t_e18"
MODELS = entity_models(entity_base(SCRATCH))
DOC, SUB_SKILL = MODELS[MachineName.DOCUMENT_VERSION], MODELS[MachineName.SUB_SKILL]
TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()
CTX = TenantContext(TENANT, PROJECT)


@pytest.fixture(scope="module")
def sessions(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        monkeypatch_module.setenv("DATABASE_URL", url(database=DB))
        command.upgrade(config(), "head")  # real audit tables + append-only trigger
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{SCRATCH}"'))
            conn.execute(text(create_frozen_function(SCRATCH)))
            next(iter(MODELS.values())).metadata.create_all(conn)
            conn.execute(text(create_frozen_trigger("t_sub_skills", SCRATCH)))
        yield make_sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


def cmd(model: type[Any], entity_id: uuid.UUID, event: str, **kw: Any) -> TransitionCommand:
    return TransitionCommand(
        model=model,
        entity_id=entity_id,
        event=event,
        actor=Actor.SYSTEM,
        actor_id=SYSTEM_ACTOR_ID,
        correlation_id="cid-int",
        **kw,
    )


def test_e1_8_t01_valid_transition_on_postgres(sessions: sessionmaker[Session]) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT, PROJECT)
    result = transition(sessions, CTX, cmd(DOC, doc, "scan_started"))
    assert (result.to_state, result.row_version) == ("SCANNING", 2)
    assert current(sessions, DOC, doc) == ("SCANNING", 2)
    [audit] = audit_rows(sessions, doc)
    assert audit.before == {"state": "UPLOADED", "row_version": 1}  # JSONB round trip
    assert audit.after == {"state": "SCANNING", "row_version": 2}


def test_e1_8_t04_concurrent_transitions_one_wins(sessions: sessionmaker[Session]) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT, PROJECT)
    start = threading.Barrier(2)
    outcomes: list[object] = []

    def worker(hold: float) -> None:
        start.wait()
        with sessions() as session:
            try:
                apply_transition(session, CTX, cmd(DOC, doc, "scan_started"))
                time.sleep(hold)  # keep the row lock while the other one tries
                session.commit()
                outcomes.append("ok")
            except ApiError as exc:
                session.rollback()
                outcomes.append(exc.status)

    threads = [threading.Thread(target=worker, args=(h,)) for h in (0.5, 0.0)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(outcomes, key=str) == [409, "ok"]  # the loser waited, then saw SCANNING
    assert current(sessions, DOC, doc) == ("SCANNING", 2)
    assert len(audit_rows(sessions, doc)) == 1


def test_e1_8_t05_audit_failure_rolls_back(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT, PROJECT)
    before_outbox = len(outbox_rows(sessions))

    def broken_record(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(service_module, "record", broken_record)
    with pytest.raises(RuntimeError):
        transition(sessions, CTX, cmd(DOC, doc, "scan_started"))
    assert current(sessions, DOC, doc) == ("UPLOADED", 1)
    assert audit_rows(sessions, doc) == []
    assert len(outbox_rows(sessions)) == before_outbox


def test_e1_8_t07_replay_is_idempotent_on_postgres(sessions: sessionmaker[Session]) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT, PROJECT)
    first = transition(sessions, CTX, cmd(DOC, doc, "scan_started", idempotency_key="wf-1:scan"))
    again = transition(sessions, CTX, cmd(DOC, doc, "scan_started", idempotency_key="wf-1:scan"))
    assert again.replayed and again.audit_id == first.audit_id
    assert len(audit_rows(sessions, doc)) == 1


def test_e1_8_frozen_row_refused_by_trigger_423(sessions: sessionmaker[Session]) -> None:
    sub = add(sessions, SUB_SKILL, "CERTIFIED", TENANT, PROJECT, is_frozen=True)
    with pytest.raises(RowFrozen):
        transition(sessions, CTX, cmd(SUB_SKILL, sub, "freeze"))
    assert current(sessions, SUB_SKILL, sub) == ("CERTIFIED", 1)
    assert audit_rows(sessions, sub) == []


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE sbam.audit_log SET action = 'x'",
        "DELETE FROM sbam.audit_log",
        "TRUNCATE sbam.audit_log CASCADE",
    ],
)
def test_e1_8_audit_log_is_append_only(sessions: sessionmaker[Session], sql: str) -> None:
    doc = add(sessions, DOC, "UPLOADED", TENANT, PROJECT)
    transition(sessions, CTX, cmd(DOC, doc, "scan_started"))
    with sessions() as session:
        with pytest.raises(DBAPIError) as err:
            session.execute(text(sql))
        session.rollback()
    assert getattr(err.value.orig, "sqlstate", None) == "SBAUD"
    assert len(audit_rows(sessions, doc)) == 1
