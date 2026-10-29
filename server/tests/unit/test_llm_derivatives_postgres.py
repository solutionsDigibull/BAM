"""#420: the derivative cache on a real PostgreSQL: its migration, JSONB column and unique-key race.

The unit tests of the cache run on SQLite, which cannot show any of these. This module needs a
scratch PostgreSQL superuser DSN in `SCHEMA_CHECK_TEST_DSN` (CI's unit job has one) and skips
without it; each test gets a database of its own, dropped afterwards.
"""

import os
import threading
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from alembic import command
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine
from sbam_api.llm.models import LlmDerivative
from sbam_api.llm.repository import LlmDerivativeRepository
from sqlalchemy import Engine, pool, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

DSN = os.environ.get("SCHEMA_CHECK_TEST_DSN")
pytestmark = pytest.mark.skipif(
    DSN is None, reason="SCHEMA_CHECK_TEST_DSN names no scratch PostgreSQL"
)
THIS = "0037_llm_derivatives"
BEFORE = "0036_workflow_schemas"
TENANT, OTHER = uuid.uuid4(), uuid.uuid4()


def values(key: str, output: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    return {
        "cache_key": key,
        "alias": "sbam-sow",
        "prompt_id": "smoke",
        "prompt_version": "1",
        "output": output or {"count": 3, "label": "ok"},
        "tokens_in": 10,
        "tokens_out": 5,
        "cost_usd": Decimal("0.5"),
        **over,
    }


class Db:
    def __init__(self, dsn: str) -> None:
        self.name = f"s420_{uuid.uuid4().hex[:12]}"
        self.admin = dsn
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.name}"')
        url = make_url(dsn.replace("postgresql://", "postgresql+psycopg://", 1))
        self.url = url.set(database=self.name).render_as_string(hide_password=False)
        self.engine: Engine = make_engine(
            self.url, poolclass=pool.NullPool, connect_args={"connect_timeout": 5}
        )
        self.sessions: sessionmaker[Session] = sessionmaker(self.engine)

    def migrate(self, direction: str, revision: str) -> None:
        before = os.environ.get("DATABASE_URL")  # alembic's env.py reads it; leave no trace
        os.environ["DATABASE_URL"] = self.url
        try:
            getattr(command, direction)(config(), revision)
        finally:
            if before is None:
                del os.environ["DATABASE_URL"]
            else:
                os.environ["DATABASE_URL"] = before

    def has_table(self) -> bool:
        with self.engine.connect() as conn:
            found = conn.execute(text("SELECT to_regclass('sbam.llm_derivatives')")).scalar()
            return found is not None

    def close(self) -> None:
        self.engine.dispose()
        with psycopg.connect(self.admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')


@pytest.fixture
def db() -> Iterator[Db]:
    database = Db(DSN or "")
    database.migrate("upgrade", THIS)  # from nothing to this revision: its own parents included
    yield database
    database.close()


def test_420_t09_the_migration_goes_down_and_up_again(db: Db) -> None:
    assert db.has_table()
    db.migrate("downgrade", BEFORE)
    assert not db.has_table()
    db.migrate("upgrade", THIS)
    assert db.has_table()


def test_420_t09_the_output_column_is_jsonb_and_round_trips_nested_json(db: Db) -> None:
    output = {"rows": [{"id": 1, "tags": ["a", "ü", "名前"], "ratio": 0.25}], "none": None}
    with db.sessions() as session:
        assert LlmDerivativeRepository(session, TenantContext(TENANT, None)).put(
            values("k" * 64, output)
        )
        session.commit()
    with db.engine.connect() as conn:
        kind = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'sbam' AND table_name = 'llm_derivatives' "
                "AND column_name = 'output'"
            )
        ).scalar()
        found = conn.execute(
            text("SELECT output->'rows'->0->'tags'->>2 FROM sbam.llm_derivatives")
        ).scalar()
    assert kind == "jsonb" and found == "名前"
    with db.sessions() as session:
        row = LlmDerivativeRepository(session, TenantContext(TENANT, None)).by_key("k" * 64)
        assert row is not None and row.output == output and row.cost_usd == Decimal("0.5")


def test_420_t10_a_second_put_of_the_same_key_loses_cleanly_on_the_unique_index(db: Db) -> None:
    key = "a" * 64
    with db.sessions() as session:
        repo = LlmDerivativeRepository(session, TenantContext(TENANT, None))
        assert repo.put(values(key, {"count": 1, "label": "first"})) is True
        session.commit()
    with db.sessions() as session:
        repo = LlmDerivativeRepository(session, TenantContext(TENANT, None))
        assert repo.put(values(key, {"count": 2, "label": "second"})) is False  # first writer wins
        session.commit()
        assert repo.by_key(key).output["label"] == "first"  # type: ignore[union-attr]


def test_420_t10_concurrent_first_writes_of_one_key_store_one_row_and_never_raise(db: Db) -> None:
    key, writers = "b" * 64, 8
    start = threading.Barrier(writers)
    won: list[bool] = []
    errors: list[BaseException] = []

    def write(n: int) -> None:
        try:
            with db.sessions() as session:
                repo = LlmDerivativeRepository(session, TenantContext(TENANT, None))
                start.wait(timeout=10)
                won.append(repo.put(values(key, {"count": n, "label": f"w{n}"})))
                session.commit()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert won.count(True) == 1 and won.count(False) == writers - 1
    with db.sessions() as session:
        rows = list(session.scalars(select(LlmDerivative).where(LlmDerivative.cache_key == key)))
        assert len(rows) == 1


def test_420_t10_the_key_is_unique_per_tenant_not_globally(db: Db) -> None:
    key = "c" * 64
    for tenant in (TENANT, OTHER):
        with db.sessions() as session:
            assert LlmDerivativeRepository(session, TenantContext(tenant, None)).put(values(key))
            session.commit()
    with db.sessions() as session:
        assert len(list(session.scalars(select(LlmDerivative)))) == 2


def test_420_t10_replace_and_a_soft_deleted_entry_overwrite_on_postgres_too(db: Db) -> None:
    key = "d" * 64
    with db.sessions() as session:
        repo = LlmDerivativeRepository(session, TenantContext(TENANT, None))
        repo.put(values(key, {"count": 1, "label": "old"}))
        assert repo.put(values(key, {"count": 2, "label": "new"}), replace=True) is True
        session.commit()
        row = repo.by_key(key)
        assert row is not None and row.output["label"] == "new"
        row.deleted_at = row.created_at
        session.commit()
        assert repo.by_key(key) is None
        assert repo.put(values(key, {"count": 3, "label": "again"})) is True  # not blocked forever
        session.commit()
        again = repo.by_key(key)
        assert again is not None and again.output["label"] == "again"
