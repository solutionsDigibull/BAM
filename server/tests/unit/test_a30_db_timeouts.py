"""A-30: server-side lock, statement and idle-in-transaction timeouts, and what a caller sees.

The pure parts run everywhere. The `real` ones need a scratch PostgreSQL 16 (set
`A30_TEST_PG_DSN=postgresql://postgres:pw@127.0.0.1:15439/postgres`); they prove the timeouts really
fire on the server, which a SQLite stand-in cannot.
"""

import os
import threading
import time
from typing import Any

import pytest
from sbam_api.db.session import API_TIMEOUTS, WORKER_TIMEOUTS, Timeouts, make_engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

PG = os.environ.get("A30_TEST_PG_DSN")
real = pytest.mark.skipif(PG is None, reason="A30_TEST_PG_DSN names no scratch PostgreSQL")
FAST = Timeouts(lock_ms=300, statement_ms=400, idle_in_transaction_ms=500)


def test_a30_t01_api_and_worker_limits_are_conservative_and_ordered() -> None:
    assert API_TIMEOUTS.lock_ms <= 5_000 and API_TIMEOUTS.statement_ms <= 30_000
    assert API_TIMEOUTS.idle_in_transaction_ms <= 60_000
    # a worker may wait longer than a request, never shorter
    assert WORKER_TIMEOUTS.lock_ms > API_TIMEOUTS.lock_ms
    assert WORKER_TIMEOUTS.statement_ms > API_TIMEOUTS.statement_ms


def _connect_args(monkeypatch: pytest.MonkeyPatch, url: str, **kwargs: Any) -> dict[str, object]:
    """The `connect_args` `make_engine` hands to SQLAlchemy (no database is contacted)."""
    from sqlalchemy import create_engine as real_create

    seen: list[dict[str, object]] = []

    def spy(*args: Any, **kw: Any) -> Any:
        seen.append(dict(kw.get("connect_args") or {}))
        return real_create(*args, **kw)

    monkeypatch.setattr("sbam_api.db.session.create_engine", spy)
    make_engine(url, **kwargs)
    return seen[0]


def test_a30_t02_the_engine_sets_them_on_every_postgres_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _connect_args(monkeypatch, "postgresql://u:p@db/x", timeouts=API_TIMEOUTS)
    options = str(args["options"])
    assert "-c lock_timeout=5000" in options and "-c statement_timeout=30000" in options
    assert "-c idle_in_transaction_session_timeout=60000" in options
    assert args["connect_timeout"] == 2


def test_a30_t03_migrations_and_scripts_set_no_limit_unless_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "options" not in _connect_args(monkeypatch, "postgresql://u:p@db/x")  # DDL may run long
    own = {"connect_timeout": 9}  # alembic passes its own connect_args: left exactly as given
    assert _connect_args(monkeypatch, "postgresql://u:p@db/x", connect_args=own) == own


def test_a30_t04_sqlite_engines_are_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _connect_args(monkeypatch, "sqlite://", timeouts=API_TIMEOUTS) == {}


@real
def test_a30_t05_the_server_reports_the_configured_limits() -> None:
    assert PG
    engine = make_engine(PG, timeouts=API_TIMEOUTS)
    with engine.connect() as conn:
        shown = {
            name: conn.execute(text(f"SHOW {name}")).scalar_one()
            for name in ("lock_timeout", "statement_timeout", "idle_in_transaction_session_timeout")
        }
    assert shown == {
        "lock_timeout": "5s",
        "statement_timeout": "30s",
        "idle_in_transaction_session_timeout": "1min",
    }


@real
def test_a30_t06_a_request_waiting_for_a_locked_row_fails_fast_not_forever() -> None:
    assert PG
    admin = make_engine(PG)
    with admin.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS a30_rows"))
        conn.execute(text("CREATE TABLE a30_rows (id int primary key)"))
        conn.execute(text("INSERT INTO a30_rows VALUES (1)"))
    holder = make_engine(PG).connect()  # the request that holds the project lock (a hung Temporal)
    holder.execute(text("SELECT * FROM a30_rows WHERE id = 1 FOR UPDATE"))
    waiter = make_engine(PG, timeouts=FAST).connect()
    started = time.monotonic()
    with pytest.raises(OperationalError) as caught:
        waiter.execute(text("SELECT * FROM a30_rows WHERE id = 1 FOR UPDATE"))
    assert caught.value.orig.sqlstate == "55P03"  # type: ignore[union-attr]
    assert time.monotonic() - started < 3  # about the 300 ms limit, not an indefinite wait
    waiter.close()
    holder.rollback()
    holder.close()


@real
def test_a30_t07_a_runaway_statement_is_cancelled() -> None:
    assert PG
    conn = make_engine(PG, timeouts=FAST).connect()
    with pytest.raises(OperationalError) as caught:
        conn.execute(text("SELECT pg_sleep(5)"))
    assert caught.value.orig.sqlstate == "57014"  # type: ignore[union-attr]
    conn.close()


@real
def test_a30_t08_a_request_idle_in_a_transaction_loses_its_lock() -> None:
    """The A-30 scenario: the request holds a row lock and waits on Temporal. The server ends the
    session, the lock is released and the next request proceeds; the pool does not stay blocked."""
    assert PG
    admin = make_engine(PG)
    with admin.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS a30_rows2"))
        conn.execute(text("CREATE TABLE a30_rows2 (id int primary key)"))
        conn.execute(text("INSERT INTO a30_rows2 VALUES (1)"))
    stuck = make_engine(PG, timeouts=FAST).connect()
    stuck.execute(text("SELECT * FROM a30_rows2 WHERE id = 1 FOR UPDATE"))  # then: no more commands
    other = make_engine(PG, timeouts=Timeouts(5_000, 5_000, 5_000)).connect()
    result: list[object] = []

    def second_request() -> None:
        other.execute(text("SELECT * FROM a30_rows2 WHERE id = 1 FOR UPDATE"))
        result.append("got the lock")

    worker = threading.Thread(target=second_request)
    started = time.monotonic()
    worker.start()
    worker.join(8)
    assert result == ["got the lock"] and time.monotonic() - started < 4  # freed after ~0.5 s
    other.rollback()
    other.close()
    with pytest.raises(DBAPIError) as dead:  # the stuck request learns it was ended
        stuck.execute(text("SELECT 1"))
    assert dead.value.orig.sqlstate == "25P03"  # type: ignore[union-attr]


# --- what a caller sees ---------------------------------------------------------------------------


class _Recorder:
    """A Temporal client stand-in that records the keyword arguments of the calls under test."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start_workflow(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("start_workflow", kwargs))
        raise RuntimeError("stop here")  # start_run maps this to OrchestratorUnavailable

    def get_workflow_handle(self, _id: str) -> "_Recorder":
        return self

    async def signal(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("signal", kwargs))

    async def describe(self, **kwargs: Any) -> Any:
        self.calls.append(("describe", kwargs))
        raise RuntimeError("stop here")


def test_a30_t09_every_temporal_call_made_under_a_lock_has_an_rpc_timeout() -> None:
    import asyncio
    import uuid

    from pydantic import BaseModel
    from sbam_api.pipeline.service import signal_pipeline
    from sbam_api.temporal.client import RPC_TIMEOUT, OrchestratorUnavailable
    from sbam_api.temporal.service import StartSpec, start_run
    from sbam_contracts.runs import RunInput, SubjectType, TaskQueue

    assert RPC_TIMEOUT.total_seconds() <= 10
    client = _Recorder()

    class Payload(BaseModel):
        x: int = 1

    asyncio.run(signal_pipeline(client, uuid.uuid4(), "sow_approved", Payload()))  # type: ignore[arg-type]
    spec = StartSpec(
        workflow="W",
        workflow_id="w-1",
        queue=TaskQueue.BUILD,
        subject_type=SubjectType.PROJECT,
        subject_id=uuid.uuid4(),
        stage="x",
        input=RunInput(
            tenant_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            correlation_id="corr-a30",
        ),
    )
    with pytest.raises(OrchestratorUnavailable):  # the stand-in fails the start: that is the 503
        asyncio.run(start_run(client, None, spec))  # type: ignore[arg-type]
    assert {name for name, _ in client.calls} == {"signal", "start_workflow"}
    for _name, kwargs in client.calls:
        assert kwargs.get("rpc_timeout") == RPC_TIMEOUT
