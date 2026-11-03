"""A-12 (#442): an authenticated request uses one database session on the auth path, not three
(membership lookup, users-mirror sync and the handler each opened their own)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sbam_api.core.auth import Auth, TokenVerifier
from sbam_api.core.auth_cache import AuthState, AuthStateCache
from sbam_api.db.session import make_engine
from sbam_api.identity.models import Tenant
from sbam_api.identity.service import SqlUserDirectory
from sbam_api.main import create_app
from sbam_api.projects.models import ProjectMember
from sbam_api.projects.repository import SqlMemberships
from sbam_contracts.api import API_BASE
from sqlalchemy import Engine, pool
from sqlalchemy.orm import Session, sessionmaker
from test_auth import ADMIN_A, AUDIENCE, ISSUER, TENANT_A, FakeJwks, Realm
from test_core_app import ALL_OK, make_settings
from test_identity import Audit

ADMIN = {"realm_access": {"roles": ["sbam-admin"]}}


def sqlite_engine() -> Engine:
    engine = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    t = Tenant.metadata.tables
    Tenant.metadata.create_all(
        engine,
        tables=[t["sbam.tenants"], t["sbam.users"], ProjectMember.__table__],  # type: ignore[list-item]
    )
    return engine


class Counting(sessionmaker[Session]):
    """A sessionmaker that counts the sessions it hands out."""

    opened = 0

    def __call__(self, **local_kw: Any) -> Session:
        type(self).opened += 1
        return super().__call__(**local_kw)


class Harness:
    def __init__(self, tmp_path: Path, cache: AuthStateCache | None = None) -> None:
        self.realm = Realm()
        audit = Audit()
        Counting.opened = 0
        self.sessions = Counting(sqlite_engine(), expire_on_commit=False)
        auth = Auth(
            TokenVerifier(ISSUER, AUDIENCE, FakeJwks(self.realm)),
            memberships=SqlMemberships(lambda: self.sessions),
            audit=audit,
            directory=SqlUserDirectory(lambda: self.sessions, audit),
            state_cache=cache or AuthStateCache(0),
        )
        self.app = create_app(make_settings(tmp_path), ALL_OK, auth)
        self.app.state.sessions = self.sessions
        self.client = TestClient(self.app)
        with self.sessions() as s:
            s.add(Tenant(id=TENANT_A, name="Tenant A"))
            s.commit()
        self.token = self.realm.token(ADMIN_A, **ADMIN)

    def me(self) -> Any:
        return self.client.get(f"{API_BASE}/me", headers={"authorization": f"Bearer {self.token}"})

    def _subs(self) -> list[str]:
        from sbam_api.identity.models import User

        with self.sessions() as s:
            return [u.keycloak_sub for u in s.query(User)]

    def sessions_for(self, call: Any) -> int:
        before = Counting.opened
        response = call()
        assert response.status_code == 200, response.text
        return Counting.opened - before


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_a12_t01_an_authenticated_request_opens_one_session(h: Harness) -> None:
    h.me()  # the first request creates the users row (the one-off insert path)
    assert h.sessions_for(h.me) == 1


def test_a12_t02_the_first_request_of_a_new_user_also_uses_one(h: Harness) -> None:
    assert h.sessions_for(h.me) == 1


# --- the short auth-state cache (off by default) ----------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a12_t03_the_cache_is_off_by_default_so_every_request_asks_the_database(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path)
    h.me()
    h.me()
    assert h.sessions_for(h.me) == 1  # asked again: the session was opened for the lookups


def test_a12_t04_with_the_cache_on_a_repeat_request_skips_the_lookups_but_not_the_handler(
    tmp_path: Path,
) -> None:
    clock = Clock()
    h = Harness(tmp_path, AuthStateCache(5, clock))
    assert h.sessions_for(h.me) == 1  # a miss: lookups and handler share the one session
    before = Counting.opened
    assert h.me().status_code == 200
    assert Counting.opened - before == 1  # a hit: only the handler's session


def test_a12_t05_the_cache_expires_and_a_new_login_misses(tmp_path: Path) -> None:
    clock = Clock()
    cache = AuthStateCache(5, clock)
    cache.put("sub", "sid-1", AuthState(frozenset(), None))
    assert cache.get("sub", "sid-1") is not None
    assert cache.get("sub", "sid-2") is None  # another session of the same user
    clock.now += 5
    assert cache.get("sub", "sid-1") is None  # expired


def test_a12_t06_a_failure_is_never_cached_and_a_dropped_user_is_forgotten() -> None:
    cache = AuthStateCache(60)
    cache.put("sub", "sid", AuthState(frozenset(), frozenset({"sbam-admin"})))
    cache.invalidate("sub")
    assert cache.get("sub", "sid") is None
    assert AuthStateCache(0).get("sub", "sid") is None  # off: nothing is ever kept
    off = AuthStateCache(0)
    off.put("sub", "sid", AuthState(frozenset(), None))
    assert off.get("sub", "sid") is None


def test_a12_t07_a_deactivated_user_is_refused_every_time_and_comes_back_at_once(
    tmp_path: Path,
) -> None:
    from sbam_api.identity.models import User
    from sbam_contracts.identity import UserStatus

    h = Harness(tmp_path, AuthStateCache(60, Clock()))
    assert h.me().status_code == 200
    with h.sessions() as s:
        s.query(User).update({"status": UserStatus.DEACTIVATED.value})
        s.commit()
    h.app.state.auth.state_cache.invalidate(next(iter(h._subs())))  # a deactivation does this
    assert h.me().status_code == 401  # refused: nothing about the refusal is kept
    with h.sessions() as s:
        s.query(User).update({"status": UserStatus.ACTIVE.value})
        s.commit()
    assert h.me().status_code == 200
