"""MUI-3 (#782) against the Compose Postgres: the master_lists migration up, down and up again, and
its constraints. A scratch database is created and dropped. Needs `make up`."""

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine
from sqlalchemy import pool, text
from sqlalchemy.exc import IntegrityError

from .test_db import url

pytestmark = pytest.mark.integration

REVISION = "0065_master_lists"
INSERT = (
    "INSERT INTO sbam.master_lists (id, tenant_id, key, headers, rows, row_version) "
    "VALUES (:id, :tenant, :key, '[\"A\"]', '[]', 1)"
)


def test_mui_3_master_lists_migration_up_down_up_and_constraints() -> None:
    db = f"sbam_mui3_{uuid.uuid4().hex[:10]}"
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db}" OWNER sbam'))
    engine = make_engine(url(database=db), poolclass=pool.NullPool)
    previous = ScriptDirectory.from_config(config()).get_revision(REVISION).down_revision
    assert isinstance(previous, str)
    tenant = uuid.uuid4()
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=db))
            command.upgrade(config(), "head")
            with engine.begin() as conn:
                conn.execute(text(INSERT), {"id": uuid.uuid4(), "tenant": tenant, "key": "assets"})
            for key in ("assets", "vendors"):  # a second list of a key; a key not in the set
                with pytest.raises(IntegrityError), engine.begin() as conn:
                    conn.execute(text(INSERT), {"id": uuid.uuid4(), "tenant": tenant, "key": key})
            command.downgrade(config(), previous)  # this revision undoes itself
            with engine.connect() as conn:
                gone = conn.execute(text("SELECT to_regclass('sbam.master_lists')")).scalar()
            assert gone is None
            command.upgrade(config(), "head")  # and up again
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)'))
        admin.dispose()
