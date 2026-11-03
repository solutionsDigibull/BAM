"""A-32 (#458): a token copied before sign-out stops working at sign-out, not at `exp`."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sbam_api.core.auth_cache import AuthStateCache
from sbam_api.identity.models import RevokedSession
from sbam_api.identity.revocation import REVOCATION_TTL, SqlSessionRevocations
from test_auth import FDE_A1, problem
from test_identity import Harness as IdentityHarness


class Harness(IdentityHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        RevokedSession.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.now = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
        self.revocations = SqlSessionRevocations(lambda: self.sessions, lambda: self.now)
        self.client.app.state.auth.revocations = self.revocations  # type: ignore[attr-defined]

    def me(self, sid: str | None = "s1", sub: uuid.UUID = FDE_A1, **claims: Any) -> Any:
        return self.call("GET", "/me", self.realm.token(sub, sid=sid, **claims))

    def logout(self, sid: str | None = "s1", sub: uuid.UUID = FDE_A1) -> Any:
        return self.call("POST", "/auth/logout", self.realm.token(sub, sid=sid))


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_a32_t01_the_token_of_a_signed_out_session_is_refused_at_once(h: Harness) -> None:
    assert h.me("s1").status_code == 200
    assert h.logout("s1").status_code == 204
    problem(h.me("s1"), 401, "UNAUTHENTICATED")


def test_a32_t02_another_session_of_the_same_user_keeps_working(h: Harness) -> None:
    assert h.me("s1").status_code == 200 and h.me("s2").status_code == 200
    h.logout("s1")
    assert h.me("s2").status_code == 200  # another browser, another `sid`
    assert h.me("s1").status_code == 401


def test_a32_t03_signing_out_again_with_the_same_token_adds_nothing(h: Harness) -> None:
    assert h.logout("s1").status_code == 204
    # the second call carries the same, now revoked, token: refused, and nothing is duplicated
    assert h.logout("s1").status_code == 401
    with h.sessions() as s:
        assert s.query(RevokedSession).count() == 1


def test_a32_t04_a_revocation_lapses_with_the_tokens_it_covers(h: Harness) -> None:
    h.logout("s1")
    assert h.revocations.is_revoked("s1")
    h.now += REVOCATION_TTL - timedelta(seconds=1)
    assert h.revocations.is_revoked("s1")
    h.now += timedelta(seconds=2)  # every token of that session has expired by now
    assert not h.revocations.is_revoked("s1")


def test_a32_t05_expired_rows_are_swept_by_the_next_sign_out(h: Harness) -> None:
    h.logout("old")
    h.now += REVOCATION_TTL + timedelta(minutes=1)
    h.revocations.revoke("new", "someone")
    with h.sessions() as s:
        assert [r.sid for r in s.query(RevokedSession)] == ["new"]


def test_a32_t06_logout_needs_a_valid_token(h: Harness) -> None:
    problem(h.call("POST", "/auth/logout", "not-a-token"), 401, "UNAUTHENTICATED")
    assert h.client.post("/api/v1/auth/logout").status_code == 401


def test_a32_t07_a_token_without_a_session_id_has_nothing_to_revoke(h: Harness) -> None:
    assert h.logout(None).status_code == 204
    assert h.me(None).status_code == 200
    with h.sessions() as s:
        assert s.query(RevokedSession).count() == 0


def test_a32_t08_a_user_can_only_sign_out_their_own_session(h: Harness) -> None:
    other = uuid.uuid4()
    assert h.me("s9", other).status_code == 200
    h.logout("s1", FDE_A1)  # the endpoint takes no id: it revokes the caller's own `sid`
    with h.sessions() as s:
        assert [r.sid for r in s.query(RevokedSession)] == ["s1"]
    assert h.me("s9", other).status_code == 200  # someone else's session is not touched


def test_a32_t09_the_auth_state_cache_cannot_keep_a_signed_out_session_alive(h: Harness) -> None:
    """With the cache on (A-12), the denylist is asked before it, so a cached state is not a way
    back in, and a logout drops the user's cached entries too."""
    auth = h.client.app.state.auth  # type: ignore[attr-defined]
    auth.state_cache = AuthStateCache(60)
    assert h.me("s1").status_code == 200
    assert auth.state_cache.get(str(FDE_A1), "s1") is not None  # cached by the first request
    h.logout("s1")
    assert auth.state_cache.get(str(FDE_A1), "s1") is None
    problem(h.me("s1"), 401, "UNAUTHENTICATED")
    assert h.me("s2").status_code == 200  # another session of the user still works
