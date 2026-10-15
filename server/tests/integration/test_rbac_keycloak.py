"""E2.4 against the real stack: live sbam-api, Postgres, Keycloak and Mailpit (TD §19.3).

T01 invites through the running API and finds the invite email in Mailpit; T08 deactivates a seeded
FDE and checks Keycloak revoked their sessions. The race test makes two admins demote themselves at
once against real Postgres. All clean up: the invited user is deleted, and the seeded users get
their seed roles and status back even when an assertion fails.
"""

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from .conftest import port
from .test_identity import API, admin_secret, keycloak_admin, sql
from .test_keycloak_realm import KC, TENANT_A, claims, login

pytestmark = pytest.mark.integration

MAILPIT = f"http://127.0.0.1:{port('MAILPIT_PORT', 8025)}/api/v1"


@pytest.fixture
def admin() -> str:
    admin_secret()  # the stack must have sbam-api-admin, or user changes answer 503
    token: str = login("sbam-admin", "admin-a")["access_token"]
    return token


def call(method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    headers = {"authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
    return httpx.request(method, f"{API}{path}", headers=headers, timeout=30, **kwargs)


@pytest.fixture
def email() -> Iterator[str]:
    value = f"e24-{uuid.uuid4().hex[:8]}@sbam.test"
    yield value
    for user in keycloak_admin("GET", f"/users?email={value}&exact=true").json():
        keycloak_admin("DELETE", f"/users/{user['id']}")
    sql("DELETE FROM sbam.users WHERE email = :e", e=value)


def test_e2_4_t01_invite_sends_the_keycloak_email(admin: str, email: str) -> None:
    res = call("POST", "/admin/users/invite", admin, json={"email": email, "roles": ["sbam-fde"]})
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "INVITED"
    user_id = res.json()["id"]

    [kc_user] = keycloak_admin("GET", f"/users?email={email}&exact=true").json()
    assert kc_user["id"] == user_id and kc_user["enabled"]
    roles = keycloak_admin("GET", f"/users/{user_id}/role-mappings/realm").json()
    assert "sbam-fde" in {r["name"] for r in roles}
    orgs = keycloak_admin("GET", f"/organizations/members/{user_id}/organizations").json()
    assert [o["alias"] for o in orgs] == [TENANT_A]

    mail = httpx.get(f"{MAILPIT}/search", params={"query": f"to:{email}"}, timeout=10).json()
    assert mail["messages_count"] >= 1
    action = sql(
        "SELECT action FROM sbam.audit_log WHERE entity_id = :id AND action = 'USER_INVITED'",
        id=user_id,
    )
    assert action


def test_e2_4_t08_deactivate_revokes_sessions(admin: str) -> None:
    tokens = login("sbam-fde", "fde-a2")
    user_id = claims(tokens)["sub"]
    assert call("GET", "/me", tokens["access_token"]).status_code == 200  # mirrored
    users = call("GET", "/admin/users", admin).json()["items"]
    version = next(u["row_version"] for u in users if u["id"] == user_id)
    try:
        res = call(
            "PATCH",
            f"/admin/users/{user_id}",
            admin,
            headers={"if-match": f'"{version}"'},
            json={"active": False},
        )
        assert res.status_code == 200, res.text
        version = res.json()["row_version"]

        assert keycloak_admin("GET", f"/users/{user_id}").json()["enabled"] is False
        assert keycloak_admin("GET", f"/users/{user_id}/sessions").json() == []
        refresh = httpx.post(
            f"{KC}/protocol/openid-connect/token",
            data={
                "grant_type": "refresh_token",
                "client_id": "sbam-fde",
                "refresh_token": tokens["refresh_token"],
            },
            timeout=30,
        )
        assert refresh.status_code == 400 and refresh.json()["error"] == "invalid_grant"
        # The still-unexpired access token is refused too.
        assert call("GET", "/me", tokens["access_token"]).status_code == 401
    finally:
        res = call(
            "PATCH",
            f"/admin/users/{user_id}",
            admin,
            headers={"if-match": f'"{version}"'},
            json={"active": True},
        )
        assert res.status_code == 200, res.text
    assert call("GET", "/me", login("sbam-fde", "fde-a2")["access_token"]).status_code == 200


PORTAL = {"super-admin", "sbam-admin", "sbam-fde"}


def db_user(user_id: str) -> tuple[list[str], int]:
    [(roles, version)] = sql(
        "SELECT portal_roles, row_version FROM sbam.users WHERE id = :id", id=user_id
    )
    return sorted(roles), version


def kc_roles(user_id: str) -> list[str]:
    mappings = keycloak_admin("GET", f"/users/{user_id}/role-mappings/realm").json()
    return sorted(r["name"] for r in mappings if r["name"] in PORTAL)


def set_roles(token: str, user_id: str, roles: list[str]) -> httpx.Response:
    return call(
        "PATCH",
        f"/admin/users/{user_id}",
        token,
        headers={"if-match": f'"{db_user(user_id)[1]}"'},
        json={"roles": roles},
    )


def test_e2_4_concurrent_demotions_keep_one_admin(admin: str) -> None:
    """Two admins demote themselves at the same moment: the tenant row lock lets one through and
    the other gets LAST_ADMIN. Keycloak's role mapping follows the DB after every change."""
    admin_a = claims(login("sbam-admin", "admin-a"))["sub"]
    fde_a2 = claims(login("sbam-fde", "fde-a2"))["sub"]
    try:
        assert set_roles(admin, fde_a2, ["sbam-admin", "sbam-fde"]).status_code == 200
        assert kc_roles(fde_a2) == db_user(fde_a2)[0] == ["sbam-admin", "sbam-fde"]

        tokens = {admin_a: admin, fde_a2: login("sbam-admin", "fde-a2")["access_token"]}
        bodies = {admin_a: ["sbam-fde"], fde_a2: ["sbam-fde"]}
        for token in tokens.values():  # a new login bumps the row once; do it before the ETags
            assert call("GET", "/me", token).status_code == 200
        headers = {uid: {"if-match": f'"{db_user(uid)[1]}"'} for uid in tokens}
        barrier = threading.Barrier(2)
        results: dict[str, httpx.Response] = {}

        def demote(uid: str) -> None:
            barrier.wait()
            results[uid] = call(
                "PATCH",
                f"/admin/users/{uid}",
                tokens[uid],
                headers=headers[uid],
                json={"roles": bodies[uid]},
            )

        threads = [threading.Thread(target=demote, args=(uid,)) for uid in tokens]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        codes = sorted(r.status_code for r in results.values())
        assert codes == [200, 409], {u: r.text for u, r in results.items()}
        loser = next(r for r in results.values() if r.status_code == 409)
        assert loser.json()["code"] == "LAST_ADMIN"
        for uid in tokens:
            assert kc_roles(uid) == db_user(uid)[0]
    finally:
        # Back to the seed: admin-a is the admin, fde-a2 an FDE. Whoever is admin restores.
        if "sbam-admin" not in db_user(admin_a)[0]:
            rescuer = login("sbam-admin", "fde-a2")["access_token"]
            assert set_roles(rescuer, admin_a, ["sbam-admin"]).status_code == 200
        if db_user(fde_a2)[0] != ["sbam-fde"]:
            restorer = login("sbam-admin", "admin-a")["access_token"]
            assert set_roles(restorer, fde_a2, ["sbam-fde"]).status_code == 200
    assert kc_roles(admin_a) == db_user(admin_a)[0] == ["sbam-admin"]
    assert kc_roles(fde_a2) == db_user(fde_a2)[0] == ["sbam-fde"]
