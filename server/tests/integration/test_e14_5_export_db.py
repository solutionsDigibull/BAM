"""E14.5 (#133) against the Compose Postgres: migration 0064 up and down, and a frozen EXPORT
artifact row that the frozen trigger keeps immutable. A scratch database is created and dropped.
Needs `make up`; not part of the unit run."""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine
from sqlalchemy import pool, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from .test_db import url

pytestmark = pytest.mark.integration

PREVIOUS = "0063_workflow_versions_uq_name"
DB = f"sbam_e145_{uuid.uuid4().hex[:10]}"


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
            command.downgrade(config(), PREVIOUS)  # removes the EXPORT rows and the kind
            with scratch.connect() as conn:
                left = conn.execute(text("SELECT count(*) FROM sbam.artifacts WHERE kind='EXPORT'"))
                assert left.scalar() == 0
            command.upgrade(config(), "head")
    finally:
        scratch.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def test_e14_5_t01_a_frozen_export_artifact_is_stored_and_immutable(engine: Engine) -> None:
    row, sha = uuid.uuid4(), "a" * 64
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sbam.artifacts (id, tenant_id, project_id, kind, storage_key, sha256,"
                " size, content_type, is_frozen, row_version) VALUES (:id, :t, :p, 'EXPORT',"
                " :key, :sha, 10, 'application/zip', true, 1)"
            ),
            {
                "id": row,
                "t": uuid.uuid4(),
                "p": uuid.uuid4(),
                "key": f"x/exports/{sha}",
                "sha": sha,
            },
        )
    with pytest.raises(DBAPIError, match="frozen"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE sbam.artifacts SET size = 11 WHERE id = :id"), {"id": row})
