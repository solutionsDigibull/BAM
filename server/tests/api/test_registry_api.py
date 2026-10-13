"""E12.1 registry API: GET /admin/registry (admin) and GET /registry (FDE), through the real app."""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from registry_db import sample_registry, sqlite_sessions
from sbam_api.core.auth import ROLE_ADMIN, ROLE_FDE, CurrentUser, admin, fde
from sbam_api.main import create_app
from sbam_api.registry.sync import sync
from sqlalchemy.orm import Session, sessionmaker
from test_core_app import ALL_OK, make_settings

ADMIN_URL, FDE_URL = "/api/v1/admin/registry", "/api/v1/registry"


def user(role: str) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(), keycloak_sub="s", tenant_id=uuid.uuid4(), roles=frozenset({role})
    )


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    return sqlite_sessions()


def client(tmp_path: Path, sessions: sessionmaker[Session], logged_in: bool = True) -> TestClient:
    app = create_app(make_settings(tmp_path), ALL_OK)
    app.state.sessions = sessions
    if logged_in:  # the token part of the chain is #48's tests; here only the policies
        app.dependency_overrides[admin] = lambda: user(ROLE_ADMIN)
        app.dependency_overrides[fde] = lambda: user(ROLE_FDE)
    return TestClient(app)


def test_e12_1_t01_admin_and_fde_read_the_active_registry(
    tmp_path: Path, sessions: sessionmaker[Session]
) -> None:
    registry = sample_registry()
    sync(sessions, registry, activate=True)
    c = client(tmp_path, sessions)
    for url in (ADMIN_URL, FDE_URL):
        res = c.get(url)
        assert res.status_code == 200, url
        body = res.json()
        assert body["image_digest"] == registry.image_digest
        assert body["content_sha256"] == registry.content_sha256
        names = [p["name"] for p in body["packages"]]
        assert "psycopg" in names and names == sorted(names)


def test_e12_1_no_registry_yet_is_404(tmp_path: Path, sessions: sessionmaker[Session]) -> None:
    c = client(tmp_path, sessions)
    assert c.get(ADMIN_URL).status_code == 404
    assert c.get(FDE_URL).json()["code"] == "NOT_FOUND"


def test_e12_1_registry_needs_a_login(tmp_path: Path, sessions: sessionmaker[Session]) -> None:
    sync(sessions, sample_registry(), activate=True)
    c = client(tmp_path, sessions, logged_in=False)
    assert c.get(ADMIN_URL).status_code == 401
    assert c.get(FDE_URL).status_code == 401
