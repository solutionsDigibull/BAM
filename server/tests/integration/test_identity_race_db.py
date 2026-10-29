"""A-16 (#429) on the Compose Postgres: the first requests of a new Keycloak session, in parallel.

Every tenant request mirrors the token onto `users` (`SqlUserDirectory.sync`). A new `sid` is a
new login; the portal fires several calls right after sign-in, and they used to race on the row's
`row_version` (`StaleDataError`, a 500). Real row locks: SQLite serialises writers and cannot
show it. A scratch database migrated to head; dropped at the end."""

import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from alembic import command
from sbam_api.core.auth import CurrentUser
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.models import Tenant, User
from sbam_api.identity.service import SqlUserDirectory
from sqlalchemy import pool, select, text
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_a16_{uuid.uuid4().hex[:10]}"
PARALLEL = 16  # the issue's acceptance: 16 requests of one new session at the same moment


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture(scope="module")
def sessions(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        monkeypatch_module.setenv("DATABASE_URL", url(database=DB))
        command.upgrade(config(), "head")
        yield make_sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


class Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def event(self, **event: Any) -> None:
        with self._lock:
            self.events.append(event)

    def denied(self, **_: Any) -> None:
        raise AssertionError("nobody is denied here")

    def actions(self, action: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["action"] == action]


class World:
    """One active tenant and one FDE who has signed in once (session s0)."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions
        self.audit = Audit()
        self.directory = SqlUserDirectory(lambda: sessions, self.audit)
        self.tenant, self.sub = uuid.uuid4(), str(uuid.uuid4())
        with sessions() as s:
            s.add(Tenant(id=self.tenant, name=f"A16 {self.sub[:8]}", status="ACTIVE"))
            s.commit()
        self.user_id = uuid.uuid4()
        self.sync("s0")
        self.audit.events.clear()

    def token(self, sid: str, email: str = "fde@a16.test") -> CurrentUser:
        return CurrentUser(
            user_id=self.user_id,
            keycloak_sub=self.sub,
            tenant_id=self.tenant,
            roles=frozenset({"sbam-fde"}),
            email=email,
            display_name="FDE",
            session_id=sid,
        )

    def sync(self, sid: str, email: str = "fde@a16.test") -> None:
        self.directory.sync(self.token(sid, email))

    def row(self) -> User:
        with self.sessions() as s:
            row = s.scalars(select(User).where(User.keycloak_sub == self.sub)).one()
            s.expunge(row)
            return row


def race(jobs: list[Any]) -> list[BaseException]:
    """All jobs start together; whatever each one raised."""
    barrier = threading.Barrier(len(jobs))

    def run(job: Any) -> BaseException | None:
        barrier.wait(timeout=30)
        try:
            job()
        except BaseException as exc:  # the test inspects every kind
            return exc
        return None

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool_:
        return [o for o in pool_.map(run, jobs) if o is not None]


def test_a16_t01_parallel_first_requests_all_succeed(sessions: sessionmaker[Session]) -> None:
    world = World(sessions)
    before = world.row().row_version
    assert race([lambda: world.sync("s1")] * PARALLEL) == []  # no 500
    assert len(world.audit.actions("LOGIN")) == 1  # one login, audited once
    row = world.row()
    assert row.last_session_id == "s1" and row.row_version == before + 1


def test_a16_t02_second_login_audited_once(sessions: sessionmaker[Session]) -> None:
    world = World(sessions)
    for sid in ("s1", "s2"):
        assert race([lambda sid=sid: world.sync(sid)] * PARALLEL) == []
    assert len(world.audit.actions("LOGIN")) == 2
    assert world.row().last_session_id == "s2"


def test_a16_a_racing_email_change_is_mirrored_and_audited_once(
    sessions: sessionmaker[Session],
) -> None:
    world = World(sessions)
    jobs = [lambda: world.sync("s0", email="new@a16.test")] * PARALLEL
    assert race(jobs) == []
    [change] = world.audit.actions("USER_EMAIL_CHANGED")
    assert change["before"] == {"email": "fde@a16.test"} and change["after"] == {
        "email": "new@a16.test"
    }
    assert world.row().email == "new@a16.test"
    assert world.audit.actions("LOGIN") == []  # same session: not a login


def test_a16_the_usual_request_writes_nothing(sessions: sessionmaker[Session]) -> None:
    world = World(sessions)
    before = world.row().row_version
    assert race([lambda: world.sync("s0")] * PARALLEL) == []
    assert world.row().row_version == before and world.audit.events == []
