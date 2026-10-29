"""Engine and sessions: sync SQLAlchemy 2 on psycopg 3 (ADR 0003)."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import State


def engine_url(database_url: str) -> str:
    """DATABASE_URL is a plain `postgresql://` URL (Compose); SQLAlchemy needs the driver."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url.removeprefix(prefix)
    return database_url


@dataclass(frozen=True)
class Timeouts:
    """Server-side limits, in milliseconds, set on every connection of an engine (A-30).

    `lock_ms`: how long a statement waits for a row lock (FOR UPDATE, UPDATE of a locked row) before
    PostgreSQL raises `lock_not_available`; `statement_ms`: how long one statement may run;
    `idle_in_transaction_ms`: how long a session may sit in an open transaction doing nothing, which
    is what a request does while it waits on Temporal holding a row lock: the server then ends the
    connection and releases the lock."""

    lock_ms: int
    statement_ms: int
    idle_in_transaction_ms: int

    def options(self) -> str:
        return (
            f"-c lock_timeout={self.lock_ms} -c statement_timeout={self.statement_ms} "
            f"-c idle_in_transaction_session_timeout={self.idle_in_transaction_ms}"
        )


# An API request answers a person: a lock or a query that is not free in seconds is a fault, and
# the row lock a request holds while it calls Temporal must not outlive that call by much.
API_TIMEOUTS = Timeouts(lock_ms=5_000, statement_ms=30_000, idle_in_transaction_ms=60_000)
# A worker activity may write a lot or wait for a busy row; its own retries and heartbeats are the
# real limits, so these only stop a connection that is stuck for good.
WORKER_TIMEOUTS = Timeouts(lock_ms=30_000, statement_ms=600_000, idle_in_transaction_ms=600_000)


def make_engine(database_url: str, *, timeouts: Timeouts | None = None, **kwargs: Any) -> Engine:
    """`timeouts=None` (migrations, one-off scripts) sets no server-side limit."""
    if database_url.startswith(("postgresql", "postgres")) and "connect_args" not in kwargs:
        connect_args: dict[str, Any] = {"connect_timeout": 2}
        if timeouts is not None:
            connect_args["options"] = timeouts.options()
        kwargs["connect_args"] = connect_args
    return create_engine(engine_url(database_url), pool_pre_ping=True, **kwargs)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def ping(engine: Engine) -> bool:
    """`SELECT 1` on the pool: the readiness check."""
    with engine.connect() as conn:
        return bool(conn.execute(text("SELECT 1")).scalar_one() == 1)


def app_sessions(state: State) -> sessionmaker[Session]:
    """The app's sessionmaker, built on first use from its settings (tests set `state.sessions`)."""
    if getattr(state, "sessions", None) is None:
        state.sessions = make_sessionmaker(
            make_engine(state.settings.database_url, timeouts=API_TIMEOUTS)
        )
    sessions: sessionmaker[Session] = state.sessions
    return sessions
