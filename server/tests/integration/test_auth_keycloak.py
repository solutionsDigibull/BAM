"""E2.2-T08/T09 against the real Keycloak: real tokens, Keycloak stopped mid-test (TD §19.3).

The app runs in-process with the host's Keycloak URLs; tokens come from the PKCE login helper
used by the realm tests (#47). Keycloak is always started again and waited for.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.settings import load_settings
from sbam_api.main import create_app
from sbam_contracts.api import API_BASE

from .conftest import Compose, port
from .test_keycloak_realm import KC, login
from .test_profiles import wait_healthy

pytestmark = pytest.mark.integration


def app_client(tmp_path: Path) -> TestClient:
    settings = load_settings(
        _env_file=None,
        database_url="postgresql://sbam@127.0.0.1:1/sbam",  # unused: no DB in this path
        storage_root=tmp_path,
        temporal_host="127.0.0.1:1",
        keycloak_issuer=KC,
        keycloak_jwks_url=f"{KC}/protocol/openid-connect/certs",
        keycloak_audience="sbam-api",
    )
    app = create_app(settings, checks={})
    router = APIRouter()

    @router.get("/whoami")
    def whoami(user: Annotated[CurrentUser, Depends(fde)]) -> dict[str, str]:
        return {"tenant_id": str(user.tenant_id)}

    app.include_router(router, prefix=API_BASE)
    return TestClient(app)


@pytest.fixture
def keycloak_stopper(compose: Compose) -> Iterator[Compose]:
    yield compose
    compose("start", "keycloak", timeout=300)
    assert wait_healthy(["keycloak"]) == {"keycloak": "healthy"}


def call(client: TestClient, token: str) -> int:
    res = client.get(f"{API_BASE}/whoami", headers={"authorization": f"Bearer {token}"})
    return res.status_code


def test_e2_2_t08_t09_keycloak_down(tmp_path: Path, keycloak_stopper: Compose) -> None:
    assert port("KEYCLOAK_PORT", 8080)
    token = login("sbam-fde", "fde-a1")["access_token"]
    warm = app_client(tmp_path)
    assert call(warm, token) == 200  # JWKS fetched and cached
    assert keycloak_stopper("stop", "keycloak", timeout=180).returncode == 0

    # T08: the cached key keeps verifying while Keycloak is down.
    assert call(warm, token) == 200

    # T09: an app that never saw this key can't verify it and can't reach Keycloak: 503, no access.
    cold = app_client(tmp_path / str(uuid.uuid4()))
    res = cold.get(f"{API_BASE}/whoami", headers={"authorization": f"Bearer {token}"})
    assert res.status_code == 503
    assert res.json()["code"] == "IDP_UNAVAILABLE"
