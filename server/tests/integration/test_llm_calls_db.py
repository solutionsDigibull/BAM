"""E6.1 against the Compose Postgres: migration 0014 up and down, the row constraints, tenant scoping
and the replay lookup. A scratch database is created and dropped. Needs `make up`; not part of the
unit run."""

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.llm.repository import LlmCallRepository
from sqlalchemy import pool, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e61_{uuid.uuid4().hex[:10]}"
TENANT, OTHER, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield make_sessionmaker(engine)
            command.downgrade(config(), "0013_registry_allowed")  # 0014 undoes exactly itself
            with engine.connect() as conn:
                found = conn.execute(text("SELECT to_regclass('sbam.llm_calls')")).scalar()
            assert found is None
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def values(activity: str, status: str = "SUCCEEDED", **over: Any) -> dict[str, Any]:
    return {
        "alias": "sbam-sow",
        "model": "sbam-sow-primary",
        "prompt_id": "smoke",
        "prompt_version": "1.0.0",
        "prompt_sha256": "a" * 64,
        "tokens_in": 10,
        "tokens_out": 5,
        "cost_usd": Decimal("0.001234"),
        "latency_ms": 42,
        "attempt": 1,
        "status": status,
        "trace_id": uuid.uuid4().hex,
        "context_manifest": {"rfq": {"chars": 3}},
        "correlation_id": "corr",
        "temporal_workflow_id": "wf",
        "activity_id": activity,
        "activity_attempt": 1,
        "output": {"count": 3} if status == "SUCCEEDED" else None,
        **over,
    }


def repo(session: Session, tenant: uuid.UUID = TENANT) -> LlmCallRepository:
    return LlmCallRepository(session, TenantContext(tenant, PROJECT))


def test_e6_1_a_row_round_trips_with_jsonb_and_exact_cost(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        row = repo(session).insert(values("a1"))
        session.commit()
        got = repo(session).get(row.id)
    assert got is not None
    assert got.output == {"count": 3} and got.context_manifest == {"rfq": {"chars": 3}}
    assert got.cost_usd == Decimal("0.001234") and got.row_version == 1


def test_e6_1_status_outside_the_enum_is_refused_by_the_check(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session, pytest.raises(IntegrityError):
        repo(session).insert(values("a2", status="DONE"))


def test_e6_1_t09_replay_lookup_finds_the_succeeded_row_of_this_activity_attempt(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo(session).insert(values("a3", status="SCHEMA_INVALID"))
        ok = repo(session).insert(values("a3"))
        session.commit()
        assert repo(session).cached("wf", "a3", 1, "smoke").id == ok.id  # type: ignore[union-attr]
        assert repo(session).cached("wf", "a3", 2, "smoke") is None  # another Temporal attempt
        assert repo(session).cached("wf", "a3", 1, "other-prompt") is None
        assert repo(session, OTHER).cached("wf", "a3", 1, "smoke") is None  # another tenant


def test_e6_1_every_attempt_of_an_activity_is_listed_in_order(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        for status in ("FAILED", "SCHEMA_INVALID", "SUCCEEDED"):
            repo(session).insert(values("a4", status=status))
        session.commit()
        assert [r.status for r in repo(session).for_activity("wf", "a4")] == [
            "FAILED",
            "SCHEMA_INVALID",
            "SUCCEEDED",
        ]
        assert repo(session, OTHER).for_activity("wf", "a4") == []
