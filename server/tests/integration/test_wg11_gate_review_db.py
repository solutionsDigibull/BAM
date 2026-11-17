"""#759 on the Compose Postgres: migration 0055 lets `gate_results` hold a REVIEW row.

A scratch database migrated to head (the real migrations). Up: REVIEW is accepted and an unknown
gate is still refused. Down: the REVIEW rows are deleted and the old constraint refuses REVIEW
again, while every other gate's row is kept. Up again: it works. The foreign keys are switched off
for the session (`session_replication_role`) so the rows need no Sub-Skill; the CHECK still runs.
"""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine
from sqlalchemy import Engine, pool, text
from sqlalchemy.exc import IntegrityError

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_wg11_{uuid.uuid4().hex[:10]}"
BEFORE = "0054_document_relevance"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    eng = make_engine(url("postgres", DB), poolclass=pool.NullPool)
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


def insert(engine: Engine, gate: str) -> uuid.UUID:
    row = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(
            text(
                "INSERT INTO sbam.gate_results (id, tenant_id, project_id, sub_skill_id, gate,"
                " status, findings, row_version, created_at, updated_at)"
                " VALUES (:id, :t, :p, :s, :gate, 'FAILED', '[]'::jsonb, 1, now(), now())"
            ),
            {"id": row, "t": uuid.uuid4(), "p": uuid.uuid4(), "s": uuid.uuid4(), "gate": gate},
        )
    return row


def count(engine: Engine, row: uuid.UUID) -> int:
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT count(*) FROM sbam.gate_results WHERE id = :id"), {"id": row}
        ).scalar()
    return int(found or 0)


def test_wg11_a_review_row_is_accepted_and_an_unknown_gate_is_not(engine: Engine) -> None:
    assert count(engine, insert(engine, "REVIEW")) == 1
    with pytest.raises(IntegrityError) as caught:
        insert(engine, "NOT_A_GATE")
    assert "gate_valid" in str(caught.value)


def test_wg11_migration_0055_goes_down_and_up_again(engine: Engine) -> None:
    review, ruff = insert(engine, "REVIEW"), insert(engine, "RUFF")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url(database=DB))
        command.downgrade(config(), BEFORE)
        assert count(engine, review) == 0  # a REVIEW row would break the old constraint
        assert count(engine, ruff) == 1  # nothing else is touched
        with pytest.raises(IntegrityError) as caught:
            insert(engine, "REVIEW")
        assert "gate_valid" in str(caught.value)
        command.upgrade(config(), "head")
    assert count(engine, insert(engine, "REVIEW")) == 1
