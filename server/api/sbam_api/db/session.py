"""Engine and sessions: sync SQLAlchemy 2 on psycopg 3 (ADR 0003)."""

from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def engine_url(database_url: str) -> str:
    """DATABASE_URL is a plain `postgresql://` URL (Compose); SQLAlchemy needs the driver."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url.removeprefix(prefix)
    return database_url


def make_engine(database_url: str, **kwargs: Any) -> Engine:
    if database_url.startswith(("postgresql", "postgres")):
        kwargs.setdefault("connect_args", {"connect_timeout": 2})
    return create_engine(engine_url(database_url), pool_pre_ping=True, **kwargs)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def ping(engine: Engine) -> bool:
    """`SELECT 1` on the pool: the readiness check."""
    with engine.connect() as conn:
        return bool(conn.execute(text("SELECT 1")).scalar_one() == 1)
