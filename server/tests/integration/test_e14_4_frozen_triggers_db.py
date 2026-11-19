"""E14.4 (#132) against the Compose Postgres: migration 0062 up and down, and the frozen trigger
(T03: tamper via SQL, even for the application role). A scratch database is created and dropped.
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

REVISION = "0062_workflow_versions"
PREVIOUS = "0061_registry_entry_kind"
DB = f"sbam_e144_{uuid.uuid4().hex[:10]}"


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
            command.downgrade(config(), PREVIOUS)  # this revision undoes itself
            with scratch.connect() as conn:
                assert (
                    conn.execute(text("SELECT to_regclass('sbam.workflow_versions')")).scalar()
                    is None
                )
                gone = conn.execute(
                    text("SELECT count(*) FROM pg_trigger WHERE tgname = 'artifacts_frozen'")
                )
                assert gone.scalar() == 0
            command.upgrade(config(), "head")  # and up again
    finally:
        scratch.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def insert_artifact(engine: Engine, frozen: bool) -> uuid.UUID:
    row = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sbam.artifacts (id, tenant_id, project_id, kind, storage_key, sha256,"
                " size, content_type, is_frozen, row_version) VALUES (:id, :t, :p, 'CODE_BUNDLE',"
                " :k, :h, 1, 'application/x-tar', :f, 1)"
            ),
            {
                "id": row,
                "t": uuid.uuid4(),
                "p": uuid.uuid4(),
                "k": f"k/{row}",
                "h": "a" * 64,
                "f": frozen,
            },
        )
    return row


def test_e14_4_t03_a_frozen_artifact_cannot_be_updated_or_deleted(engine: Engine) -> None:
    frozen = insert_artifact(engine, True)
    for statement in (
        "UPDATE sbam.artifacts SET size = 2 WHERE id = :id",
        "DELETE FROM sbam.artifacts WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="frozen"), engine.begin() as conn:
            conn.execute(text(statement), {"id": frozen})
    with engine.connect() as conn:
        size = conn.execute(text("SELECT size FROM sbam.artifacts WHERE id = :id"), {"id": frozen})
        assert size.scalar() == 1  # 0 rows changed


def test_e14_4_an_artifact_that_is_not_frozen_can_still_be_changed(engine: Engine) -> None:
    open_row = insert_artifact(engine, False)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE sbam.artifacts SET is_frozen = true WHERE id = :id"), {"id": open_row}
        )
    with pytest.raises(DBAPIError, match="frozen"), engine.begin() as conn:
        conn.execute(text("UPDATE sbam.artifacts SET size = 3 WHERE id = :id"), {"id": open_row})


def test_e14_4_the_workflow_versions_table_has_the_trigger_and_the_unique_version(
    engine: Engine,
) -> None:
    with engine.connect() as conn:
        names = conn.execute(
            text("SELECT tgname FROM pg_trigger WHERE tgrelid = 'sbam.workflow_versions'::regclass")
        )
        assert "workflow_versions_frozen" in {n for (n,) in names}
        unique = conn.execute(
            text(
                "SELECT count(*) FROM pg_constraint"
                " WHERE conname = 'uq_workflow_versions_workflow_id'"
            )
        )
        assert unique.scalar() == 1
