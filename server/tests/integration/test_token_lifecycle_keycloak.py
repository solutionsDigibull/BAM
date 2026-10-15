"""E2.7-T06 against the running stack: log out, then try to refresh (#53).

The in-process half (expired, forged, deactivated, suspended) is
tests/security/test_token_lifecycle.py.
This half needs the real Keycloak, because logout and refresh live there. Not run by `make test`.
"""

import httpx
import pytest

from .test_identity import API
from .test_keycloak_realm import KC, claims, login

pytestmark = pytest.mark.integration

TOKEN_URL = f"{KC}/protocol/openid-connect/token"
LOGOUT_URL = f"{KC}/protocol/openid-connect/logout"


def refresh(token: str) -> httpx.Response:
    return httpx.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "client_id": "sbam-fde", "refresh_token": token},
        timeout=30,
    )


def me(access_token: str) -> httpx.Response:
    return httpx.get(f"{API}/me", headers={"authorization": f"Bearer {access_token}"}, timeout=30)


def test_e2_7_t06_logout_revokes_refresh_and_the_access_token_is_short_lived() -> None:
    tokens = login("sbam-fde", "fde-a2")
    access = tokens["access_token"]
    assert me(access).status_code == 200

    out = httpx.post(
        LOGOUT_URL,
        data={"client_id": "sbam-fde", "refresh_token": tokens["refresh_token"]},
        timeout=30,
    )
    assert out.status_code in (200, 204), out.text

    # The refresh token died with the session: no new access token can be minted.
    again = refresh(tokens["refresh_token"])
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"
    # The API verifies tokens statelessly, so the access token already issued is bounded by its
    # lifetime rather than by the logout. Pin that bound; the 401 after expiry is proven in-process
    # (test_e2_7_t06_expired_token_is_401) with a token whose exp has passed.
    c = claims(tokens)
    assert c["exp"] - c["iat"] <= 300


def test_e2_7_t06_refreshed_access_token_is_accepted_by_the_api() -> None:
    tokens = login("sbam-fde", "fde-a2")

    renewed = refresh(tokens["refresh_token"])

    assert renewed.status_code == 200, renewed.text
    assert me(renewed.json()["access_token"]).status_code == 200
