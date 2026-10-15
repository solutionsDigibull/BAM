"""E2.8 `GET /admin/audit`: tenant-scoped, read-only, newest first, filters and a signed cursor.

Runs on the E2.4 harness (SQLite, fake Keycloak): the E2.4 actions write the rows read here.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sbam_api.audit.repository import record
from test_auth import ADMIN_A, FDE_A1, problem
from test_rbac import FDE, Harness

E24_ACTIONS = {
    "USER_INVITED",
    "ROLE_CHANGED",
    "USER_DEACTIVATED",
    "USER_REACTIVATED",
    "PROJECT_MEMBERS_CHANGED",
}
SECRET_KEYS = ("password", "secret", "token", "authorization", "credential")


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def audit(h: Harness, token: str | None = None, **params: str) -> Any:
    return h.call("GET", "/admin/audit", token or h.admin_a(), params=params)


def invite(h: Harness, email: str = "fde3@acme.com") -> str:
    res = h.call(
        "POST", "/admin/users/invite", h.admin_a(), json={"email": email, "roles": ["sbam-fde"]}
    )
    assert res.status_code == 201, res.text
    user_id: str = res.json()["id"]
    return user_id


def every_e24_action(h: Harness) -> None:
    invite(h)
    assert h.patch(FDE_A1, {"roles": ["sbam-admin", "sbam-fde"]}).status_code == 200
    assert h.patch(FDE_A1, {"active": False}, version=2).status_code == 200
    assert h.patch(FDE_A1, {"active": True}, version=3).status_code == 200
    assert h.assign(h.project(), FDE_A1).status_code == 200


def test_e2_8_t01_filter_by_entity_after_an_invite(h: Harness) -> None:
    user_id = invite(h)
    res = audit(h, entity_type="user", entity_id=user_id)
    assert res.status_code == 200, res.text
    [row] = res.json()["items"]
    assert row["action"] == "USER_INVITED" and row["actor_id"] == str(ADMIN_A)
    assert row["before"] is None
    assert row["after"] == {"email": "fde3@acme.com", "roles": ["sbam-fde"]}
    assert row["correlation_id"] and row["created_at"]


def test_e2_8_t02_another_tenant_and_platform_rows_are_never_returned(h: Harness) -> None:
    user_id = invite(h)
    other = audit(h, h.admin_b(), entity_type="user", entity_id=user_id)
    assert other.status_code == 200 and other.json()["items"] == []
    assert audit(h, h.admin_b()).json()["items"] == []  # tenant B wrote nothing yet
    with h.sessions() as s:  # a platform-level row (no tenant), e.g. a super-admin denial
        record(
            s,
            tenant_id=None,
            actor_id="root",
            action="access_denied",
            entity_type="route",
            entity_id="/admin/tenants",
            correlation_id="c-platform",
        )
        s.commit()
    actions = {r["action"] for r in audit(h).json()["items"]}
    assert "access_denied" not in actions and "USER_INVITED" in actions


@pytest.mark.parametrize(
    ("params", "path"),
    [
        ({"from": "2026-10-15T00:00:00Z", "to": "2026-10-01T00:00:00Z"}, "from"),  # T03
        ({"from": "2026-01-01T00:00:00Z", "to": "2026-10-01T00:00:00Z"}, "from"),  # > 92 days
        ({"to": "yesterday"}, "to"),
        ({"from": "2026-10-15T00:00:00"}, "from"),  # no timezone
        ({"limit": "0"}, "limit"),
        ({"limit": "101"}, "limit"),
        ({"entity_type": "User; DROP"}, "entity_type"),
        ({"actor": "x" * 256}, "actor"),
    ],
)
def test_e2_8_t03_bad_query_is_400_on_the_parameter(
    h: Harness, params: dict[str, str], path: str
) -> None:
    body = problem(audit(h, **params), 400, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == [path]


def test_e2_8_t04_fde_is_refused(h: Harness) -> None:
    problem(audit(h, h.fde_a1()), 403, "WRONG_PORTAL")


@pytest.mark.parametrize("email", ["a@b", '"x"@'])
def test_e2_8_t05_invite_rejects_a_non_rfc5322_email(h: Harness, email: str) -> None:
    res = h.call(
        "POST", "/admin/users/invite", h.admin_a(), json={"email": email, "roles": ["sbam-fde"]}
    )
    body = problem(res, 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["email"]
    assert h.users.calls == []  # Keycloak never called


def test_e2_8_every_e24_action_can_be_queried(h: Harness) -> None:
    every_e24_action(h)
    actions = {r["action"] for r in audit(h, limit="100").json()["items"]}
    assert actions >= E24_ACTIONS


def test_e2_8_no_secrets_in_before_or_after(h: Harness) -> None:
    every_e24_action(h)

    def keys(value: Any) -> Iterator[str]:
        if isinstance(value, dict):
            for k, v in value.items():
                yield str(k).lower()
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    rows = audit(h, limit="100").json()["items"]
    found = [k for r in rows for k in keys([r["before"], r["after"]])]
    assert found and not [k for k in found if any(s in k for s in SECRET_KEYS)]


def test_e2_8_pages_newest_first_with_a_signed_cursor(h: Harness) -> None:
    every_e24_action(h)
    everything = [r["id"] for r in audit(h, limit="100").json()["items"]]
    assert everything == sorted(everything, reverse=True)  # uuid7: newest first
    seen: list[str] = []
    cursor = None
    while True:
        body = audit(h, limit="2", **({"cursor": cursor} if cursor else {})).json()
        seen += [r["id"] for r in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == everything
    first = audit(h, limit="1").json()["next_cursor"]
    problem(audit(h, cursor=first[:-2] + "zz"), 400, "INVALID_CURSOR")
    problem(audit(h, h.admin_b(), cursor=first), 400, "INVALID_CURSOR")  # tenant A's cursor


def test_e2_8_actor_and_time_filters(h: Harness) -> None:
    other_admin = uuid.uuid4()
    assert h.call("GET", "/me", h.realm.token(other_admin, **FDE)).status_code == 200
    every_e24_action(h)
    mine = audit(h, actor=str(ADMIN_A), limit="100").json()["items"]
    assert mine and {r["actor_id"] for r in mine} == {str(ADMIN_A)}
    assert audit(h, actor=str(other_admin)).json()["items"] == []

    now = datetime.now(UTC)
    recent = audit(h, **{"from": (now - timedelta(minutes=5)).isoformat()}).json()["items"]
    assert recent
    old = audit(h, to=(now - timedelta(days=30)).isoformat()).json()["items"]
    assert old == []
