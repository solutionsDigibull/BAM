"""E15.3-T06 frozen tamper (#137) against the Compose Postgres: raw SQL on a frozen row fails with
the trigger's SQLSTATE SB423 and changes 0 rows, and every table with an `is_frozen` column has the
trigger. A scratch database at Alembic head is created and dropped. Needs `make up`.
"""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sbam_api.db.frozen import FROZEN_SQLSTATE
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine
from sqlalchemy import pool, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e153_{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    scratch = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield scratch
    finally:
        scratch.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def frozen_artifact(engine: Engine) -> uuid.UUID:
    row = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sbam.artifacts (id, tenant_id, project_id, kind, storage_key, sha256,"
                " size, content_type, is_frozen, row_version) VALUES (:id, :t, :p, 'CODE_BUNDLE',"
                " :k, :h, 1, 'application/x-tar', true, 1)"
            ),
            {"id": row, "t": uuid.uuid4(), "p": uuid.uuid4(), "k": f"k/{row}", "h": "a" * 64},
        )
    return row


def state(engine: Engine, row: uuid.UUID) -> tuple[object, ...]:
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT size, sha256, storage_key, is_frozen FROM sbam.artifacts WHERE id = :id"),
            {"id": row},
        ).one()
    return tuple(found)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sbam.artifacts SET sha256 = repeat('b', 64) WHERE id = :id",
        "UPDATE sbam.artifacts SET is_frozen = false WHERE id = :id",
        "DELETE FROM sbam.artifacts WHERE id = :id",
    ],
)
def test_e15_3_t06_sql_on_a_frozen_row_raises_sb423_and_changes_nothing(
    engine: Engine, statement: str
) -> None:
    row = frozen_artifact(engine)
    before = state(engine, row)

    with pytest.raises(DBAPIError) as error, engine.begin() as conn:
        conn.execute(text(statement), {"id": row})

    assert getattr(error.value.orig, "sqlstate", None) == FROZEN_SQLSTATE
    assert state(engine, row) == before


def test_e15_3_t06_every_table_with_is_frozen_has_the_frozen_trigger(engine: Engine) -> None:
    with engine.connect() as conn:
        tables = set(
            conn.scalars(
                text(
                    "SELECT table_name FROM information_schema.columns"
                    " WHERE table_schema = 'sbam' AND column_name = 'is_frozen'"
                )
            )
        )
        guarded = set(
            conn.scalars(
                text(
                    "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                    " JOIN pg_proc p ON p.oid = t.tgfoid"
                    " WHERE p.proname = 'forbid_frozen_change' AND NOT t.tgisinternal"
                )
            )
        )
    assert tables, "no table has is_frozen: the check would be vacuous"
    assert tables - guarded == set()
