"""A-08 (#554): the per-user request budget shared by several sbam-api replicas.

The PostgreSQL tests need a scratch superuser DSN in `SCHEMA_CHECK_TEST_DSN` (CI's unit job has
one) and skip without it; each gets a database of its own, migrated to head, dropped afterwards.
The rest need no database.
"""

import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from sbam_api.core.conventions import ApiError
from sbam_api.core.ratelimit import RateLimiter, SharedBudget
from sbam_api.core.settings import Settings, load_settings
from sbam_api.db.migrate import config
from sbam_api.db.rate_windows import count_hit, rate_windows, sweep
from sbam_api.db.session import make_engine
from sbam_api.main import create_app
from sqlalchemy import Engine, pool, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

DSN = os.environ.get("SCHEMA_CHECK_TEST_DSN")
THIS, BEFORE = "0048_rate_windows", "0047_users_idp_sync"
ANA, BOB = str(uuid.uuid4()), str(uuid.uuid4())


class Db:
    """A scratch database at head, with `replicas` engines on it (one per simulated replica)."""

    def __init__(self, dsn: str) -> None:
        self.name = f"a08c_{uuid.uuid4().hex[:12]}"
        self.admin = dsn
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.name}"')
        url = make_url(dsn.replace("postgresql://", "postgresql+psycopg://", 1))
        self.url = url.set(database=self.name).render_as_string(hide_password=False)
        self.engines: list[Engine] = []

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

    def replica(self, writes: int, llm: int) -> SharedBudget:
        engine = make_engine(self.url, poolclass=pool.NullPool)
        self.engines.append(engine)
        sessions = sessionmaker(engine)
        return SharedBudget(writes, llm, lambda: sessions)

    def scalar(self, sql: str) -> object:
        with self.engines[0].connect() as conn:
            return conn.execute(text(sql)).scalar()

    def close(self) -> None:
        for engine in self.engines:
            engine.dispose()
        with psycopg.connect(self.admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')


@pytest.fixture
def db() -> Iterator[Db]:
    if DSN is None:
        pytest.skip("SCHEMA_CHECK_TEST_DSN names no scratch PostgreSQL")
    database = Db(DSN)
    database.migrate("upgrade", "head")
    database.engines.append(make_engine(database.url, poolclass=pool.NullPool))  # for `scalar`
    yield database
    database.close()


def test_a08c_t01_two_replicas_share_one_budget_not_twice_the_limit(db: Db) -> None:
    one, two = db.replica(5, 2), db.replica(5, 2)
    waits = [(one if n % 2 == 0 else two).hit("w", ANA) for n in range(9)]
    assert waits[:5] == [0.0] * 5  # the configured number passes, spread over both replicas
    assert all(1 <= w <= 60 for w in waits[5:])  # then Retry-After: the seconds left in the minute
    assert one.hit("w", BOB) == 0.0  # another user has a budget of their own
    assert [one.hit("l", ANA), two.hit("l", ANA), one.hit("l", ANA)][:2] == [0.0, 0.0]


def test_a08c_t02_a_concurrent_burst_from_two_replicas_lets_exactly_the_limit_through(
    db: Db,
) -> None:
    replicas, allowed, lock = [db.replica(10, 10), db.replica(10, 10)], [], threading.Lock()
    start = threading.Barrier(40)

    def fire(n: int) -> None:
        start.wait()
        wait = replicas[n % 2].hit("w", ANA)
        with lock:
            allowed.append(wait == 0.0)

    threads = [threading.Thread(target=fire, args=(n,)) for n in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(allowed) == 40 and sum(allowed) == 10


def test_a08c_t03_finished_minutes_are_swept_and_the_current_one_is_kept(db: Db) -> None:
    replica = db.replica(5, 2)
    replica.hit("w", ANA)
    with replica.sessions()() as session:
        session.execute(
            rate_windows.insert(),
            [
                {"bucket": "w:old", "minute": 1, "hits": 3},
                {"bucket": "w:older", "minute": 2, "hits": 1},
            ],
        )
        assert sweep(session) == 2
        session.commit()
        rows = session.execute(select(rate_windows.c.bucket)).scalars().all()
    assert rows == [f"w:{ANA}"]


def test_a08c_t04_the_migration_goes_down_and_up_and_the_table_is_unlogged(db: Db) -> None:
    assert (
        db.scalar("SELECT relpersistence FROM pg_class WHERE oid = 'sbam.rate_windows'::regclass")
        == "u"
    )
    db.migrate("downgrade", BEFORE)
    assert db.scalar("SELECT to_regclass('sbam.rate_windows')") is None
    db.migrate("upgrade", THIS)
    assert db.scalar("SELECT to_regclass('sbam.rate_windows')") is not None


def test_a08c_t05_count_hit_counts_per_bucket_and_minute(db: Db) -> None:
    replica = db.replica(5, 2)
    with replica.sessions()() as session:
        assert [count_hit(session, "w:x")[0] for _ in range(3)] == [1, 2, 3]
        assert count_hit(session, "w:y")[0] == 1
        assert 1 <= count_hit(session, "w:x")[1] <= 60
        session.commit()


class Denying:
    """A SharedBudget stand-in: refuses with `wait` seconds, and records what it was asked."""

    def __init__(self, wait: float) -> None:
        self.wait = wait
        self.asked: list[tuple[str, str]] = []

    def hit(self, kind: str, user_id: str) -> float:
        self.asked.append((kind, user_id))
        return self.wait


def limiter(shared: object, writes: int = 100, llm: int = 100) -> RateLimiter:
    return RateLimiter(writes, llm, shared=shared)  # type: ignore[arg-type]


def settings(tmp_path: Path, **over: object) -> Settings:
    return load_settings(
        _env_file=None,
        database_url="postgresql://sbam@127.0.0.1:1/sbam",
        storage_root=tmp_path,
        temporal_host="127.0.0.1:1",
        keycloak_issuer="http://127.0.0.1:1/realms/sbam",
        keycloak_audience="sbam-api",
        **over,
    )


def test_a08c_t06_the_shared_budget_answers_429_with_retry_after() -> None:
    shared = Denying(12.2)
    with pytest.raises(ApiError) as caught:
        limiter(shared).check(ANA, "POST", "/api/v1/projects/p/documents")
    assert caught.value.status == 429 and caught.value.headers == {"Retry-After": "13"}
    assert shared.asked == [("w", ANA)]  # not an LLM route: the LLM budget is not touched


def test_a08c_t07_an_llm_route_spends_both_shared_budgets_and_a_read_spends_none() -> None:
    shared = Denying(0.0)
    rate = limiter(shared)
    rate.check(ANA, "POST", "/api/v1/projects/p/sow/regenerate")
    assert shared.asked == [("w", ANA), ("l", ANA)]
    rate.check(ANA, "GET", "/api/v1/projects")
    assert len(shared.asked) == 2


def test_a08c_t08_the_local_window_is_judged_first_and_spares_the_database() -> None:
    shared = Denying(0.0)
    rate = limiter(shared, writes=1)
    rate.check(ANA, "POST", "/api/v1/x")
    with pytest.raises(ApiError):  # the 429, asserted in t06
        rate.check(ANA, "POST", "/api/v1/x")
    assert shared.asked == [("w", ANA)]  # the second request never reached the database


def test_a08c_t09_an_unreachable_table_means_the_process_judges_alone() -> None:
    def broken() -> sessionmaker[Session]:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    shared = SharedBudget(5, 2, broken)
    assert shared.hit("w", ANA) == 0.0  # no exception: a budget must not take the API down
    limiter(shared).check(ANA, "POST", "/api/v1/x")


def test_a08c_t10_replicas_above_one_turn_the_shared_budget_on(tmp_path: Path) -> None:
    one = create_app(settings(tmp_path), {})
    many = create_app(settings(tmp_path, sbam_api_replicas=2, cursor_secret="k" * 32), {})
    assert one.state.limiter.shared is None
    assert isinstance(many.state.limiter.shared, SharedBudget)
