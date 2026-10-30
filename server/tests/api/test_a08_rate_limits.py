"""A-08 (#434): per-user request budgets, Retry-After, upload rejections as one audit row, and the
`access_denied` audit throttle. The app is the real one (fake JWKS, SQLite); the limiter is swapped
for one with a tiny budget where a test needs to reach it quickly."""

import uuid
from pathlib import Path

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.audit.security import AuditLogSecurityAudit
from sbam_api.core.auth import CurrentUser
from sbam_api.core.ratelimit import MAX_KEYS, AuditThrottle, RateLimiter, SlidingWindow
from sqlalchemy import select
from test_auth import FDE_A1, FDE_A2
from test_core_app import make_settings
from test_uploads import Harness as UploadsHarness


@pytest.fixture
def h(tmp_path: Path) -> UploadsHarness:
    return UploadsHarness(tmp_path)


def limit(h: UploadsHarness, writes: int, llm: int) -> None:
    h.client.app.state.limiter = RateLimiter(writes, llm)  # type: ignore[attr-defined]


def rows(h: UploadsHarness, action: str) -> int:
    with h.sessions() as s:
        return sum(1 for r in s.scalars(select(AuditLog)) if r.action == action)


def test_a08_t01_25_reject_requests_add_at_most_25_audit_rows(h: UploadsHarness) -> None:
    before = len(list(h.sessions().scalars(select(AuditLog))))
    for _ in range(25):
        res = h.announce(h.files(*[f"bad{n}.exe" for n in range(10)]))
        assert res.status_code == 415, res.text  # ten rejected files, one request
    assert rows(h, "UPLOAD_REJECTED") == 25  # was 250
    assert len(list(h.sessions().scalars(select(AuditLog)))) - before <= 25


def test_a08_t01b_the_row_names_the_rejected_files_capped(h: UploadsHarness) -> None:
    h.announce(h.files(*[f"bad{n}.exe" for n in range(20)]))
    with h.sessions() as s:
        [row] = [r for r in s.scalars(select(AuditLog)) if r.action == "UPLOAD_REJECTED"]
    assert row.after is not None and row.after["count"] == 20
    assert len(row.after["files"]) == 10 and row.after["files"][0]["code"] == "UNSUPPORTED_TYPE"


def test_a08_t01c_a_mixed_request_records_one_rejection_row_and_the_accepted_files(
    h: UploadsHarness,
) -> None:
    body = h.files("good.pdf", "bad.exe")
    assert h.announce(body).status_code == 201
    assert rows(h, "UPLOAD_REJECTED") == 1 and rows(h, "UPLOAD_ACCEPTED") == 1


def test_a08_t02_writes_past_the_budget_are_429_with_retry_after(h: UploadsHarness) -> None:
    limit(h, writes=5, llm=100)
    codes = [h.announce(h.files("a.exe")).status_code for _ in range(7)]
    assert codes[:5] == [415] * 5 and codes[5:] == [429, 429]
    blocked = h.announce(h.files("a.exe"))
    assert blocked.json()["code"] == "RATE_LIMITED"
    assert 1 <= int(blocked.headers["retry-after"]) <= 60


def test_a08_t03_reads_and_other_users_are_not_limited(h: UploadsHarness) -> None:
    limit(h, writes=2, llm=100)
    for _ in range(3):
        h.announce(h.files("a.exe"))
    assert h.call("GET", "/projects", h.fde()).status_code != 429  # reads are free
    other = h.call(
        "POST", f"/projects/{h.project}/uploads", h.realm.token(FDE_A2), json=h.files("a.exe")
    )
    assert other.status_code != 429  # another user has their own budget


def test_a08_t04_llm_routes_have_their_own_smaller_budget(h: UploadsHarness) -> None:
    limit(h, writes=100, llm=2)
    start = f"/projects/{h.project}/pipeline/start"
    seen = [
        h.call("POST", start, h.fde(), headers={"Idempotency-Key": f"k{n}"}).status_code
        for n in range(4)
    ]
    assert 429 not in seen[:2] and seen[2:] == [429, 429]
    assert h.announce(h.files("a.exe")).status_code == 415  # ordinary writes still go through


def test_a08_t05_a_forged_token_spends_nobody_elses_budget(h: UploadsHarness) -> None:
    limit(h, writes=2, llm=100)
    for _ in range(10):
        res = h.call("POST", f"/projects/{h.project}/uploads", "not-a-token", json=h.files("a.exe"))
        assert res.status_code == 401
    assert h.announce(h.files("a.exe")).status_code == 415  # the real user still has a budget


def test_a08_t06_the_window_slides_and_retry_after_is_honest() -> None:
    now = [100.0]
    limiter = RateLimiter(2, 100, clock=lambda: now[0])
    limiter.check("u", "POST", "/x")
    now[0] = 130.0
    limiter.check("u", "POST", "/x")
    with pytest.raises(Exception) as err:
        limiter.check("u", "POST", "/x")
    assert err.value.headers["Retry-After"] == "30"  # type: ignore[attr-defined]
    now[0] = 160.1  # the first hit left the window
    limiter.check("u", "POST", "/x")


def test_a08_t07_zero_turns_a_budget_off_and_settings_take_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    window = SlidingWindow(0)
    assert all(window.hit("u", 1.0) == 0.0 for _ in range(1000))
    s = make_settings(tmp_path)
    assert (s.rate_limit_writes_per_minute, s.rate_limit_llm_per_minute) == (120, 10)
    monkeypatch.setenv("RATE_LIMIT_WRITES_PER_MINUTE", "7")
    s = make_settings(tmp_path)
    assert s.rate_limit_writes_per_minute == 7
    assert s.audit_denied_per_minute == 30


def test_a08_t08_the_table_of_callers_is_bounded() -> None:
    window = SlidingWindow(5)
    for n in range(MAX_KEYS + 500):
        window.hit(f"user-{n}", float(n % 3))
    assert len(window._hits) <= MAX_KEYS


def denied(audit: AuditLogSecurityAudit, user: CurrentUser, n: int) -> None:
    audit.denied(actor=user, route="/projects/x", target=str(uuid.uuid4()), reason=f"probe {n}")


def test_a08_t09_denial_audit_rows_are_throttled_per_actor_and_minute(h: UploadsHarness) -> None:
    clock = [6000.0]
    throttle = AuditThrottle(30, clock=lambda: clock[0])
    audit = AuditLogSecurityAudit(lambda: h.sessions, throttle)
    user = CurrentUser(user_id=FDE_A1, keycloak_sub="a", tenant_id=None, roles=frozenset())
    other = CurrentUser(user_id=FDE_A2, keycloak_sub="b", tenant_id=None, roles=frozenset())
    for n in range(100):
        denied(audit, user, n)
    assert rows(h, "access_denied") == 30
    denied(audit, other, 0)
    assert rows(h, "access_denied") == 31  # another actor is not starved by the first
    clock[0] += 60
    denied(audit, user, 101)
    assert rows(h, "access_denied") == 32  # a new minute, a new allowance


def test_a08_t10_throttled_denials_are_still_logged(
    h: UploadsHarness, caplog: pytest.LogCaptureFixture
) -> None:
    audit = AuditLogSecurityAudit(lambda: h.sessions, AuditThrottle(1))
    user = CurrentUser(user_id=FDE_A1, keycloak_sub="a", tenant_id=None, roles=frozenset())
    with caplog.at_level("INFO"):
        for n in range(3):
            denied(audit, user, n)
    assert rows(h, "access_denied") == 1
    assert (
        sum(
            "access denied" in r.getMessage().lower() or "denied" in r.getMessage().lower()
            for r in caplog.records
        )
        >= 3
    )
