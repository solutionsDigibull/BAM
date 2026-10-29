"""E0.15 on the Compose Postgres: every state column's CHECK constraint, read back from the
database, equals its state machine, and the database refuses any other value.

A scratch database migrated to head (the real migrations, not `create_all`), dropped at the end.
If a machine gains or loses a state and no migration follows, T01 fails here.
"""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sbam_api.db.base import SCHEMA
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine
from sbam_contracts.state_machines import check_constraint_values
from sqlalchemy import Engine, pool, text
from sqlalchemy.exc import IntegrityError
from state_checks import StatefulTable, check_values, drift, stateful_tables

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e015_{uuid.uuid4().hex[:10]}"
TABLES = stateful_tables()

_CONSTRAINTS = text("""
    SELECT c.conname, pg_get_constraintdef(c.oid)
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
     WHERE n.nspname = :schema AND t.relname = :table AND c.contype = 'c'
    """)


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture(scope="module")
def engine(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[Engine]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        monkeypatch_module.setenv("DATABASE_URL", url(database=DB))
        command.upgrade(config(), "head")
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def checks(engine: Engine, table: str) -> dict[str, str]:
    """name -> definition of the table's CHECK constraints, as PostgreSQL stores them."""
    with engine.connect() as conn:
        rows = conn.execute(_CONSTRAINTS, {"schema": SCHEMA, "table": table})
        return {str(name): str(definition) for name, definition in rows}


@pytest.mark.parametrize("t", TABLES, ids=lambda t: t.table)
def test_e0_15_t01_the_constraint_equals_the_machine(engine: Engine, t: StatefulTable) -> None:
    found = checks(engine, t.table)
    assert t.constraint in found, f"{t.table}: no {t.constraint} (did a migration drop it?)"
    database = check_values(found[t.constraint])
    machine = check_constraint_values(t.machine)
    assert not drift(database, machine), (
        f"{t.table} vs machine {t.machine.value}: {drift(database, machine)}; "
        "add a migration that recreates the CHECK constraint"
    )


@pytest.mark.parametrize("t", TABLES, ids=lambda t: t.table)
def test_e0_15_t02_the_database_refuses_any_other_state(engine: Engine, t: StatefulTable) -> None:
    """A copy of the table with only its state CHECK (NOT NULLs and the other CHECKs dropped), so
    a row holding nothing but a state tests exactly that constraint."""
    others = [name for name in checks(engine, t.table) if name != t.constraint]
    with engine.connect() as conn:
        conn.execute(
            text(f'CREATE TEMP TABLE probe (LIKE "{SCHEMA}"."{t.table}" INCLUDING CONSTRAINTS)')
        )
        for name in others:
            conn.execute(text(f'ALTER TABLE probe DROP CONSTRAINT "{name}"'))
        columns = conn.execute(
            text(
                "SELECT attname FROM pg_attribute WHERE attrelid = 'probe'::regclass "
                "AND attnum > 0 AND NOT attisdropped AND attnotnull"
            )
        ).scalars()
        for column in list(columns):
            conn.execute(text(f'ALTER TABLE probe ALTER COLUMN "{column}" DROP NOT NULL'))
        insert = text("INSERT INTO probe (state) VALUES (:state)")
        for state in check_constraint_values(t.machine):
            conn.execute(insert, {"state": state})  # every state of the machine is accepted
        with pytest.raises(IntegrityError, match=t.constraint):
            with conn.begin_nested():
                conn.execute(insert, {"state": "NOT_A_STATE"})
        conn.rollback()
