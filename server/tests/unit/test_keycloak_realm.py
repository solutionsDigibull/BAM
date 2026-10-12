"""E2.1 static checks on the committed realm export (compose/keycloak/realm-bullm.json)."""

import json
from pathlib import Path
from typing import Any

import pytest

REALM_FILE = Path(__file__).resolve().parents[3] / "compose/keycloak/realm-bullm.json"
REALM: dict[str, Any] = json.loads(REALM_FILE.read_text(encoding="utf-8"))
CLIENTS = {c["clientId"]: c for c in REALM["clients"]}


def walk(node: object, path: str = "") -> list[tuple[str, object]]:
    """Every (path, value) pair in the document."""
    out: list[tuple[str, object]] = [(path, node)]
    if isinstance(node, dict):
        for k, v in node.items():
            out += walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += walk(v, f"{path}[{i}]")
    return out


def test_e2_1_realm_and_exactly_two_public_pkce_clients() -> None:
    assert REALM["realm"] == "bullm" and REALM["organizationsEnabled"] is True
    assert set(CLIENTS) == {"sbam-admin", "sbam-fde"}
    for c in CLIENTS.values():
        assert c["publicClient"] is True  # no client secret to manage or leak
        assert c["attributes"]["pkce.code.challenge.method"] == "S256"
        assert c["standardFlowEnabled"] is True
        assert c["implicitFlowEnabled"] is False
        assert c["directAccessGrantsEnabled"] is False
        assert c["serviceAccountsEnabled"] is False


@pytest.mark.parametrize("client", ["sbam-admin", "sbam-fde"])
def test_e2_1_redirect_uris_and_origins_are_exact(client: str) -> None:
    c = CLIENTS[client]
    portal = client.removeprefix("sbam-")
    web = "http://localhost:${WEB_PORT:3000}"
    assert c["redirectUris"] == [f"{web}/api/auth/callback/keycloak-{portal}"]
    assert c["webOrigins"] == [web]
    assert c["attributes"]["post.logout.redirect.uris"] == f"{web}/login-{portal}"
    uris = [*c["redirectUris"], *c["webOrigins"], c["attributes"]["post.logout.redirect.uris"]]
    assert not any("*" in u or u == "+" for u in uris)


def test_e2_1_exactly_three_realm_roles() -> None:
    assert {r["name"] for r in REALM["roles"]["realm"]} == {"super-admin", "sbam-admin", "sbam-fde"}


def test_e2_1_tenant_and_audience_mappers() -> None:
    scopes = {s["name"]: s for s in REALM["clientScopes"]}
    tenant = scopes["organization"]["protocolMappers"][0]  # the name matters: organization:<alias>
    assert tenant["protocolMapper"] == "oidc-organization-membership-mapper"
    assert tenant["config"]["claim.name"] == "tenant_id"
    assert tenant["config"]["multivalued"] == "true"
    assert tenant["config"]["access.token.claim"] == "true"
    for c in CLIENTS.values():
        assert "organization" in c["defaultClientScopes"] and "roles" in c["defaultClientScopes"]
        audience = [
            m for m in c["protocolMappers"] if m["protocolMapper"] == "oidc-audience-mapper"
        ]
        assert audience and audience[0]["config"]["included.custom.audience"] == "sbam-api"


def test_e2_1_token_session_and_brute_force_policy() -> None:
    assert REALM["accessTokenLifespan"] == 300
    assert REALM["revokeRefreshToken"] is True and REALM["refreshTokenMaxReuse"] == 0
    assert REALM["ssoSessionIdleTimeout"] == 1800
    assert REALM["bruteForceProtected"] is True and REALM["permanentLockout"] is False
    assert REALM["failureFactor"] == 5 and REALM["maxDeltaTimeSeconds"] == 900
    assert REALM["registrationAllowed"] is False


def test_e2_1_t08_export_has_no_users_credentials_or_secrets() -> None:
    assert "users" not in REALM and "organizations" not in REALM  # seed lives in keycloak/seed.sh
    keys = {path.rsplit(".", 1)[-1].lower() for path, _ in walk(REALM)}
    assert not keys & {"credentials", "secret", "clientsecret", "password", "hashediterations"}
    assert "change-me" not in REALM_FILE.read_text(encoding="utf-8")
