"""The shared request budget's table (A-08, #554): one counter per (bucket, minute).

Not a tenant table and not a TenantScopedRepository: the bucket is `<kind>:<user id>` and the rows
live for a few minutes. Created UNLOGGED by the migration: a crash may forget a minute of counts,
which only lets a few extra requests through, and nothing is written to the WAL. `minute` is the
database clock's minute, so replicas with skewed clocks still agree.
"""

from typing import Final

from sqlalchemy import BigInteger, Column, Integer, String, Table, delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from sbam_api.db.base import Base

KEEP_MINUTES: Final = 5  # counters older than this are swept

rate_windows = Table(
    "rate_windows",
    Base.metadata,
    Column("bucket", String(80), primary_key=True),
    Column("minute", BigInteger, primary_key=True),
    Column("hits", Integer, nullable=False),
)

_MINUTE = func.floor(func.extract("epoch", func.now()) / 60).cast(BigInteger)


def count_hit(session: Session, bucket: str) -> tuple[int, int]:
    """Add one hit to the bucket's current minute: (hits so far this minute, seconds until it ends).
    PostgreSQL only (`INSERT ... ON CONFLICT DO UPDATE`); the caller commits."""
    stmt = (
        pg_insert(rate_windows)
        .values(bucket=bucket[:80], minute=_MINUTE, hits=1)
        .on_conflict_do_update(
            index_elements=[rate_windows.c.bucket, rate_windows.c.minute],
            set_={"hits": rate_windows.c.hits + 1},
        )
        .returning(
            rate_windows.c.hits,
            (60 - func.extract("epoch", func.now()).cast(BigInteger) % 60).label("left"),
        )
    )
    hits, left = session.execute(stmt).one()
    return int(hits), int(left)


def sweep(session: Session) -> int:
    """Delete the counters of finished minutes; the number removed. The caller commits."""
    stmt = delete(rate_windows).where(rate_windows.c.minute < _MINUTE - KEEP_MINUTES)
    return int(session.execute(stmt).rowcount)  # type: ignore[attr-defined]
