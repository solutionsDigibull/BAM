"""E2.3 against the real stack: live sbam-api, Postgres and Keycloak (TD §19.3).

T01/T07 create tenants through the running API, so the stack needs KEYCLOAK_ADMIN_CLIENT_SECRET
(keycloak-seed creates the sbam-api-admin client with it). Each test uses its own tenant name and
removes what it created. T06 races two first requests against Postgres itself.
"""

import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sbam_api.core.auth import CurrentUser, LogSecurityAudit
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.service import SqlUserDirectory
from sqlalchemy import text

from .conftest import ENV_FILE, Compose, port
from .test_db import url as db_url
from .test_keycloak_realm import KC, login
from .test_profiles import wait_healthy

pytestmark = pytest.mark.integration

API = f"http://127.0.0.1:{port('API_PORT', 8000)}/api/v1"
TENANT_A = uuid.UUID("6a1f3e2c-0b7d-4c55-9a51-1c2f0a7e4b11")


def admin_secret() -> str:
    """The sbam-api-admin secret: the shell (Compose reads it first), else the env file."""
    value = os.environ.get("KEYCLOAK_ADMIN_CLIENT_SECRET", "")
    for line in Path(ENV_FILE).read_text(encoding="utf-8").splitlines():
        if not value and line.startswith("KEYCLOAK_ADMIN_CLIENT_SECRET="):
            value = line.split("=", 1)[1].strip()
    if not value:
        if os.environ.get("CI"):
            pytest.fail("KEYCLOAK_ADMIN_CLIENT_SECRET is not set for the stack")
        pytest.skip("KEYCLOAK_ADMIN_CLIENT_SECRET not set: tenant creation is off in this stack")
    return value


def sql(query: str, **params: Any) -> list[Any]:
    with make_engine(db_url()).begin() as conn:
        result = conn.execute(text(query), params)
        return list(result) if result.returns_rows else []


def keycloak_admin(method: str, path: str) -> httpx.Response:
    """The Keycloak admin API as sbam-api-admin (the same service account the API uses)."""
    token = httpx.post(
        f"{KC}/protocol/openid-connect/token",
        data={"grant_type": "client_credentials"},
        auth=("sbam-api-admin", admin_secret()),
    ).json()["access_token"]
    admin = KC.replace("/realms/", "/admin/realms/", 1)
    return httpx.request(method, f"{admin}{path}", headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def root() -> str:
    admin_secret()
    token: str = login("sbam-admin", "super-admin")["access_token"]
    return token


@pytest.fixture
def name() -> Iterator[str]:
    value = f"E23 {uuid.uuid4().hex[:8]}"
    yield value
    for (org_id,) in sql("SELECT keycloak_org_id FROM sbam.tenants WHERE name = :n", n=value):
        keycloak_admin("DELETE", f"/organizations/{org_id}")
    sql("DELETE FROM sbam.tenants WHERE name = :n", n=value)


def test_e2_3_t01_create_tenant_creates_the_keycloak_organization(root: str, name: str) -> None:
    res = httpx.post(
        f"{API}/admin/tenants", json={"name": name}, headers={"authorization": f"Bearer {root}"}
    )
    assert res.status_code == 201, res.text
    tenant = res.json()
    assert tenant["status"] == "ACTIVE"
    organization = keycloak_admin("GET", f"/organizations/{tenant['keycloak_org_id']}")
    assert organization.status_code == 200
    assert organization.json()["alias"] == tenant["id"]
    assert organization.json()["attributes"]["sbam_tenant_id"] == [tenant["id"]]
    audit = "SELECT action FROM sbam.audit_log WHERE entity_type = 'tenant' AND entity_id = :id"
    assert sql(audit, id=tenant["id"]) == [("TENANT_CREATED",)]  # audit_log (#41)
    # T02 on the real unique index: same name, other case and spaces.
    dup = httpx.post(
        f"{API}/admin/tenants",
        json={"name": f"  {name.upper()} "},
        headers={"authorization": f"Bearer {root}"},
    )
    assert dup.status_code == 409 and dup.json()["code"] == "DUPLICATE_NAME"


def test_e2_3_t05_first_login_mirrors_the_user() -> None:
    tokens = login("sbam-fde", "fde-a1")
    res = httpx.get(f"{API}/me", headers={"authorization": f"Bearer {tokens['access_token']}"})
    assert res.status_code == 200, res.text
    me = res.json()
    assert me["tenant"]["id"] == str(TENANT_A)
    assert me["user"]["portal_roles"] == ["sbam-fde"]
    rows = sql(
        "SELECT tenant_id, portal_roles FROM sbam.users WHERE keycloak_sub = :s",
        s=me["user"]["keycloak_sub"],
    )
    assert rows == [(TENANT_A, ["sbam-fde"])]
    logins = "SELECT count(*) FROM sbam.audit_log WHERE action = 'LOGIN' AND actor_id = :s"
    assert sql(logins, s=me["user"]["keycloak_sub"])[0][0] >= 1


def test_e2_3_t06_parallel_first_requests_make_one_row() -> None:
    sub = uuid.uuid4()
    user = CurrentUser(
        user_id=sub, keycloak_sub=str(sub), tenant_id=TENANT_A, roles=frozenset({"sbam-fde"})
    )
    sessions = make_sessionmaker(make_engine(db_url()))
    directory = SqlUserDirectory(lambda: sessions, LogSecurityAudit())
    start, errors = threading.Barrier(2), []

    def first_request() -> None:
        start.wait()
        try:
            directory.sync(user)
        except Exception as exc:  # collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=first_request) for _ in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert sql("SELECT count(*) FROM sbam.users WHERE keycloak_sub = :s", s=str(sub)) == [(1,)]
    finally:
        sql("DELETE FROM sbam.users WHERE keycloak_sub = :s", s=str(sub))


@pytest.fixture
def keycloak_stopper(compose: Compose) -> Iterator[Compose]:
    yield compose
    compose("start", "keycloak", timeout=300)
    assert wait_healthy(["keycloak"]) == {"keycloak": "healthy"}


def test_e2_3_t07_keycloak_down_leaves_no_tenant(
    root: str, name: str, keycloak_stopper: Compose
) -> None:
    # The API verifies the token with its cached keys; the organization call itself fails.
    assert httpx.get(f"{API}/admin/tenants", headers={"authorization": f"Bearer {root}"}).is_success
    assert keycloak_stopper("stop", "keycloak", timeout=180).returncode == 0
    res = httpx.post(
        f"{API}/admin/tenants",
        json={"name": name},
        headers={"authorization": f"Bearer {root}"},
        timeout=30,
    )
    assert res.status_code == 503 and res.json()["code"] == "IDP_UNAVAILABLE"
    assert sql("SELECT count(*) FROM sbam.tenants WHERE name = :n", n=name) == [(0,)]
