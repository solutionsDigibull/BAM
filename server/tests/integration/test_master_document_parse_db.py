"""E3.8b against the Compose Postgres: migration 0028 (the parse result of a master document and
the documents of a snapshot). A scratch database is created and dropped."""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine
from sqlalchemy import Engine, pool, text

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e38b_{uuid.uuid4().hex[:10]}"
NEW_DOCUMENT_COLUMNS = ("parse_code", "parse_report", "derived_md_key", "derived_json_key")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    eng = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'sbam' AND table_name = :table"
            ),
            {"table": table},
        )
        return {row[0] for row in rows}


def test_e3_8b_the_master_document_and_snapshot_columns_exist(engine: Engine) -> None:
    assert set(NEW_DOCUMENT_COLUMNS) <= columns(engine, "master_documents")
    assert "documents" in columns(engine, "master_snapshots")


def test_e3_8b_a_snapshot_row_without_documents_gets_an_empty_list(engine: Engine) -> None:
    with engine.connect() as conn:
        nullable, default = conn.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns"
                " WHERE table_schema = 'sbam' AND table_name = 'master_snapshots'"
                " AND column_name = 'documents'"
            )
        ).one()
    assert nullable == "NO" and "'[]'" in default  # existing rows and new ones start as []


def test_e3_8b_migration_0028_goes_down_and_up_again(engine: Engine) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url(database=DB))
        command.downgrade(config(), "0027_document_archive_path")
        assert not set(NEW_DOCUMENT_COLUMNS) & columns(engine, "master_documents")
        assert "documents" not in columns(engine, "master_snapshots")
        command.upgrade(config(), "head")
    assert set(NEW_DOCUMENT_COLUMNS) <= columns(engine, "master_documents")
