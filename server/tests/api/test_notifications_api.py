"""E10.4 `/notifications` through the real app and auth chain: user scoping, paging, read state."""

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from notifications_db import add_notification, notification_sessions, rows
from sbam_api.core.auth import ROLE_ADMIN, ROLE_FDE, CurrentUser, authenticated
from sbam_api.main import create_app
from sqlalchemy.orm import Session, sessionmaker
from test_core_app import ALL_OK, make_settings

TENANT_A, TENANT_B = uuid.uuid4(), uuid.uuid4()
URL = "/api/v1/notifications"


def user(
    tenant: uuid.UUID | None, user_id: uuid.UUID | None = None, role: str = ROLE_FDE
) -> CurrentUser:
    return CurrentUser(
        user_id=user_id or uuid.uuid4(),
        keycloak_sub="sub",
        tenant_id=tenant,
        roles=frozenset({role}),
    )


Build = Callable[[], tuple[FastAPI, TestClient, sessionmaker[Session]]]


@pytest.fixture
def build(tmp_path: Path) -> Build:
    def make() -> tuple[FastAPI, TestClient, sessionmaker[Session]]:
        app = create_app(make_settings(tmp_path), ALL_OK)
        sessions = notification_sessions()
        app.state.sessions = sessions
        return app, TestClient(app), sessions

    return make


def sign_in(app: FastAPI, who: CurrentUser) -> None:
    app.dependency_overrides[authenticated] = lambda: who


def test_e10_4_t01_api_list_unread_returns_the_two_newest_first(build: Build) -> None:
    app, client, sessions = build()
    me = user(TENANT_A)
    sign_in(app, me)
    first = add_notification(sessions, TENANT_A, me.user_id, title="first")
    second = add_notification(sessions, TENANT_A, me.user_id, title="second", read=True)
    third = add_notification(sessions, TENANT_A, me.user_id, title="third")
    res = client.get(URL, params={"unread_only": "true"})
    assert res.status_code == 200
    body = res.json()
    assert [n["id"] for n in body["items"]] == [str(third), str(first)]
    assert body["next_cursor"] is None
    assert all(n["read_at"] is None for n in body["items"])
    everything = client.get(URL).json()["items"]
    assert [n["id"] for n in everything] == [str(third), str(second), str(first)]
    assert set(everything[0]) == {
        "id", "project_id", "workflow_id", "type", "title", "body", "link", "created_at", "read_at"
    }  # fmt: skip


def test_e10_4_list_pages_with_an_opaque_cursor_and_hides_other_users(build: Build) -> None:
    app, client, sessions = build()
    me, other = user(TENANT_A), user(TENANT_A)
    sign_in(app, me)
    mine = [add_notification(sessions, TENANT_A, me.user_id, title=f"n{i}") for i in range(5)]
    add_notification(sessions, TENANT_A, other.user_id, title="not mine")
    add_notification(sessions, TENANT_B, me.user_id, title="other tenant, same user id")
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        params: dict[str, str | int] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get(URL, params=params).json()
        seen += [n["id"] for n in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [str(i) for i in reversed(mine)]  # newest first, no gaps, no repeats
    bad = client.get(URL, params={"cursor": "not-a-cursor!"})
    assert bad.status_code == 400 and bad.json()["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize("limit", ["0", "101", "-1", "abc"])
def test_e10_4_t06_api_limit_out_of_range_is_422(build: Build, limit: str) -> None:
    app, client, _ = build()
    sign_in(app, user(TENANT_A))
    res = client.get(URL, params={"limit": limit})
    assert res.status_code == 422
    assert res.headers["content-type"].startswith("application/problem+json")
    assert client.get(URL, params={"limit": 1}).status_code == 200
    assert client.get(URL, params={"limit": 100}).status_code == 200


def test_e10_4_t04_api_mark_read_idor_is_404_and_leaves_the_row_alone(build: Build) -> None:
    app, client, sessions = build()
    a, b = user(TENANT_A), user(TENANT_A)
    bs = add_notification(sessions, TENANT_A, b.user_id)
    sign_in(app, a)
    res = client.post(f"{URL}/{bs}/read")
    assert res.status_code == 404 and res.json()["code"] == "NOT_FOUND"
    assert client.post(f"{URL}/{uuid.uuid4()}/read").status_code == 404  # missing looks the same
    [row] = rows(sessions, b.user_id)
    assert row.read_at is None and row.row_version == 1
    sign_in(app, b)
    assert client.post(f"{URL}/{bs}/read").status_code == 204
    assert rows(sessions, b.user_id)[0].read_at is not None


def test_e10_4_cross_tenant_user_id_collision_is_404(build: Build) -> None:
    app, client, sessions = build()
    me = user(TENANT_A)
    theirs = add_notification(sessions, TENANT_B, me.user_id)  # same user id, other tenant
    sign_in(app, me)
    assert client.post(f"{URL}/{theirs}/read").status_code == 404
    assert client.get(URL).json()["items"] == []
    assert client.post(f"{URL}/read-all").status_code == 204
    assert rows(sessions)[0].read_at is None  # read-all never reaches across tenants


def test_e10_4_read_sets_read_at_once(build: Build) -> None:
    app, client, sessions = build()
    me = user(TENANT_A)
    sign_in(app, me)
    one = add_notification(sessions, TENANT_A, me.user_id)
    assert client.post(f"{URL}/{one}/read").status_code == 204
    [row] = rows(sessions)
    first_read, version = row.read_at, row.row_version
    assert first_read is not None
    assert client.post(f"{URL}/{one}/read").status_code == 204
    [again] = rows(sessions)
    assert (again.read_at, again.row_version) == (first_read, version)


def test_e10_4_t07_api_read_all_twice_is_204_both_times(build: Build) -> None:
    app, client, sessions = build()
    me, other = user(TENANT_A), user(TENANT_A)
    sign_in(app, me)
    for _ in range(3):
        add_notification(sessions, TENANT_A, me.user_id)
    theirs = add_notification(sessions, TENANT_A, other.user_id)
    assert client.post(f"{URL}/read-all").status_code == 204
    assert client.post(f"{URL}/read-all").status_code == 204
    assert client.get(URL, params={"unread_only": "true"}).json()["items"] == []
    assert all(r.read_at is not None for r in rows(sessions, me.user_id))
    [other_row] = [r for r in rows(sessions) if r.id == theirs]
    assert other_row.read_at is None  # only my own


def test_e10_4_read_all_with_zero_unread_is_204(build: Build) -> None:
    app, client, _ = build()
    sign_in(app, user(TENANT_A))
    assert client.post(f"{URL}/read-all").status_code == 204


def test_e10_4_any_signed_in_user_has_an_inbox_but_a_tenantless_one_gets_404(build: Build) -> None:
    app, client, _ = build()
    sign_in(app, user(TENANT_A, role=ROLE_ADMIN))
    assert client.get(URL).status_code == 200
    sign_in(app, user(None))
    assert client.get(URL).status_code == 404


def test_e10_4_requires_authentication(build: Build) -> None:
    _, client, _ = build()
    assert client.get(URL).status_code == 401
    assert client.post(f"{URL}/read-all").status_code == 401
