"""E2.1 realm `bullm` against the running Keycloak: PKCE logins, token claims, hardening (TD §16).

Logs in the way a browser does (authorization code + PKCE, Organizations' identity-first pages)
as the seed users from compose/keycloak/seed.sh. Nothing here creates or changes realm data.
"""

import base64
import hashlib
import html
import json
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from .conftest import REPO, port
from .test_profiles import env_value

pytestmark = pytest.mark.integration

KC = f"http://localhost:{port('KEYCLOAK_PORT', 8080)}/realms/bullm"
WEB = f"http://localhost:{port('WEB_PORT', 3000)}"
SEED = (REPO / "compose/keycloak/seed.sh").read_text(encoding="utf-8")
TENANT_A = re.search(r"^TENANT_A=(\S+)", SEED, re.M)[1]  # type: ignore[index]
ROLES = {"super-admin", "sbam-admin", "sbam-fde"}


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def callback(client: str) -> str:
    return f"{WEB}/api/auth/callback/keycloak-{client.removeprefix('sbam-')}"


def authorize(http: "Browser | httpx.Client", client: str, **extra: str) -> httpx.Response:
    params = {
        "client_id": client,
        "response_type": "code",
        "scope": "openid",
        "redirect_uri": callback(client),
        "state": secrets.token_hex(8),
        **extra,
    }
    return http.get(f"{KC}/protocol/openid-connect/auth?{urlencode(params)}")


class Browser:
    """Carries Keycloak's cookies like a browser does. They are `Secure`; browsers still send them
    to http://localhost (a secure context), but Python's cookie jar won't, so keep them here."""

    def __init__(self) -> None:
        self.http = httpx.Client(timeout=30)
        self.cookies: dict[str, str] = {}

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *_: object) -> None:
        self.http.close()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.http.cookies.clear()
        cookie = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        res = self.http.request(method, url, headers={"Cookie": cookie} if cookie else {}, **kwargs)
        for header in res.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            self.cookies[name.strip()] = rest.split(";", 1)[0]
        return res

    def get(self, url: str) -> httpx.Response:
        return self.request("GET", url)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)


def login(client: str, username: str) -> dict[str, Any]:
    """Authorization code + PKCE through the login pages; returns the token response."""
    verifier, challenge = pkce()
    with Browser() as http:
        page = authorize(http, client, code_challenge=challenge, code_challenge_method="S256")
        for _ in range(4):  # identity-first: username page, then password page
            assert page.status_code == 200, page.text[:300]
            form = re.search(r'<form[^>]*action="([^"]+)"', page.text)
            assert form, "no login form"
            fields = {"username": username, "password": env_value("SEED_USER_PASSWORD")}
            page = http.post(html.unescape(form[1]), data=fields)
            location = page.headers.get("location", "")
            if location.startswith(callback(client)):
                code = parse_qs(urlsplit(location).query)["code"][0]
                break
        else:
            pytest.fail(f"login for {username} never reached the callback")
        token = http.post(
            f"{KC}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback(client),
                "client_id": client,
                "code_verifier": verifier,
            },
        )
    assert token.status_code == 200, token.text
    result: dict[str, Any] = token.json()
    return result


def part(jwt: str, index: int) -> dict[str, Any]:
    raw = jwt.split(".")[index]
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    return data


def claims(tokens: dict[str, Any]) -> dict[str, Any]:
    return part(tokens["access_token"], 1)


def test_e2_1_t01_fde_token_claims() -> None:
    tokens = login("sbam-fde", "fde-a1")
    c = claims(tokens)
    assert part(tokens["access_token"], 0)["alg"] == "RS256"
    assert c["iss"] == KC
    assert c["tenant_id"] == [TENANT_A]
    assert set(c["realm_access"]["roles"]) & ROLES == {"sbam-fde"}
    aud = c["aud"] if isinstance(c["aud"], list) else [c["aud"]]
    assert "sbam-api" in aud
    assert c["exp"] - c["iat"] == 300
    assert c["sub"]


def test_e2_1_t02_admin_token_claims() -> None:
    c = claims(login("sbam-admin", "admin-a"))
    assert set(c["realm_access"]["roles"]) & ROLES == {"sbam-admin"}
    assert c["tenant_id"] == [TENANT_A]


@pytest.mark.parametrize("username", ["super-admin", "fde-ab"])
def test_e2_1_no_single_org_means_no_tenant_claim(username: str) -> None:
    # super-admin has no organization; fde-ab has two and selected none. The API refuses both (#48).
    client = "sbam-admin" if username == "super-admin" else "sbam-fde"
    assert "tenant_id" not in claims(login(client, username))


def test_e2_1_t04_pkce_is_required() -> None:
    with httpx.Client(timeout=30) as http:
        res = authorize(http, "sbam-fde")  # no code_challenge
    query = parse_qs(urlsplit(res.headers.get("location", "")).query)
    assert res.status_code == 302 and query.get("error") == ["invalid_request"]
    assert "code" not in query


def test_e2_1_t05_redirect_uri_tampering_is_refused() -> None:
    _, challenge = pkce()
    with httpx.Client(timeout=30) as http:
        res = authorize(
            http,
            "sbam-fde",
            redirect_uri="https://evil.example/cb",
            code_challenge=challenge,
            code_challenge_method="S256",
        )
    assert res.status_code == 400
    assert "evil.example" not in res.headers.get("location", "")
    assert "Invalid parameter: redirect_uri" in res.text


def test_e2_1_t06_password_grant_is_disabled() -> None:
    res = httpx.post(
        f"{KC}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "sbam-admin",
            "username": "admin-a",
            "password": env_value("SEED_USER_PASSWORD"),
        },
        timeout=30,
    )
    assert res.status_code in (400, 401)
    assert res.json()["error"] == "unauthorized_client"


def test_e2_1_t07_refresh_token_rotation() -> None:
    tokens = login("sbam-fde", "fde-a2")

    def refresh(token: str) -> httpx.Response:
        return httpx.post(
            f"{KC}/protocol/openid-connect/token",
            data={"grant_type": "refresh_token", "client_id": "sbam-fde", "refresh_token": token},
            timeout=30,
        )

    first = refresh(tokens["refresh_token"])
    assert first.status_code == 200
    reused = refresh(tokens["refresh_token"])
    assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"


def test_e2_1_t03_realm_export_round_trip(tmp_path: Path) -> None:
    """Import the committed realm into an empty Keycloak, export it, and check nothing was lost."""
    image = re.search(r"image: (quay.io/keycloak/keycloak:\S+)", SEED_COMPOSE)[1]  # type: ignore[index]
    script = (
        "/opt/keycloak/bin/kc.sh import --file /in/realm-bullm.json >&2 && "
        "/opt/keycloak/bin/kc.sh export --realm bullm --file /tmp/out.json >&2 && cat /tmp/out.json"
    )
    result = subprocess.run(  # noqa: S603 - fixed docker argv
        [  # noqa: S607
            "docker",
            "run",
            "--rm",
            "-e",
            "WEB_PORT=3000",
            "-v",
            f"{REPO / 'compose/keycloak'}:/in:ro",
            "--entrypoint",
            "/bin/bash",
            image,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    exported = json.loads(result.stdout)
    committed = json.loads(
        (REPO / "compose/keycloak/realm-bullm.json")
        .read_text(encoding="utf-8")
        .replace("${WEB_PORT:3000}", "3000")
    )
    assert_subset(committed, exported, "realm")


SEED_COMPOSE = (REPO / "compose/compose.override.yaml").read_text(encoding="utf-8")
_IDENTITY = ("clientId", "name")


def assert_subset(expected: Any, actual: Any, path: str) -> None:
    """Every value in `expected` appears in `actual` (Keycloak adds ids and defaults on export)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        for key, value in expected.items():
            assert key in actual, f"{path}.{key} missing from export"
            assert_subset(value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), path
        for item in expected:
            if isinstance(item, dict):
                ident = next(k for k in _IDENTITY if k in item)
                match = [a for a in actual if isinstance(a, dict) and a.get(ident) == item[ident]]
                assert match, f"{path}: no {ident}={item[ident]} in export"
                assert_subset(item, match[0], f"{path}[{item[ident]}]")
            else:
                assert item in actual, f"{path}: {item!r} missing from export"
    else:
        assert str(expected).lower() == str(actual).lower(), f"{path}: {expected!r} != {actual!r}"
