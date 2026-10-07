"""E0.13 API test cases against a local RSA key and a fake JWKS endpoint (no Keycloak needed)."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import create_app
from auth import TokenVerifier

ISSUER = "http://kc.test/realms/bullm"
AUDIENCE = "sbam-api"


class Realm:
    """Signing keys plus a JWKS the fake client serves; `down` simulates Keycloak being stopped."""

    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {}
        self.down = False
        self.fetches = 0
        self.rotate("kid-1")

    def rotate(self, kid: str) -> None:
        self.keys = {kid: rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.kid = kid

    def jwks(self) -> dict[str, Any]:
        out = []
        for kid, key in self.keys.items():
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
            out.append({**jwk, "kid": kid, "use": "sig", "alg": "RS256"})
        return {"keys": out}

    def token(self, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "user-1",
            "azp": "sbam-fde",
            "iat": now,
            "exp": now + 300,
            "preferred_username": "fde-a",
            "realm_access": {"roles": ["sbam-fde"]},
            "tenant_id": ["tenant-a"],
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.keys[self.kid], "RS256", headers={"kid": self.kid})


class FakeJwks(jwt.PyJWKClient):
    def __init__(self, realm: Realm) -> None:
        super().__init__("http://kc.test/certs", lifespan=300)
        self.realm = realm

    def fetch_data(self) -> Any:
        self.realm.fetches += 1
        if self.realm.down:
            raise jwt.PyJWKClientConnectionError("connection refused")
        data = self.realm.jwks()
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data


@pytest.fixture
def realm() -> Realm:
    return Realm()


@pytest.fixture
def client(realm: Realm) -> TestClient:
    return TestClient(create_app(TokenVerifier(ISSUER, AUDIENCE, FakeJwks(realm))))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def assert_401(resp: Any) -> None:
    assert resp.status_code == 401
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["status"] == 401


def test_e0_13_t01_me_returns_tenant_and_role(client: TestClient, realm: Realm) -> None:
    resp = client.get("/me", headers=bearer(realm.token()))
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == "tenant-a"
    assert resp.json()["roles"] == ["sbam-fde"]


def test_missing_token_401(client: TestClient) -> None:
    assert_401(client.get("/me"))


def test_e0_13_t02_expired_token_401(client: TestClient, realm: Realm) -> None:
    five_min_ago = int(time.time()) - 300
    assert_401(client.get("/me", headers=bearer(realm.token(exp=five_min_ago))))


def test_e0_13_t02_expiry_within_leeway_accepted(client: TestClient, realm: Realm) -> None:
    thirty_s_ago = int(time.time()) - 30
    assert client.get("/me", headers=bearer(realm.token(exp=thirty_s_ago))).status_code == 200


def test_e0_13_t03_alg_none_401(client: TestClient, realm: Realm) -> None:
    claims = jwt.decode(realm.token(), options={"verify_signature": False})
    forged = f"{b64({'alg': 'none', 'typ': 'JWT', 'kid': realm.kid})}.{b64(claims)}."
    assert_401(client.get("/me", headers=bearer(forged)))


def test_e0_13_t03_hs256_with_public_key_401(client: TestClient, realm: Realm) -> None:
    claims = jwt.decode(realm.token(), options={"verify_signature": False})
    public_pem = (
        realm.keys[realm.kid]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    header = b64({"alg": "HS256", "typ": "JWT", "kid": realm.kid})
    body = b64(claims)
    sig = hmac.new(public_pem, f"{header}.{body}".encode(), hashlib.sha256).digest()
    forged = f"{header}.{body}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"
    assert_401(client.get("/me", headers=bearer(forged)))


def test_tampered_signature_401(client: TestClient, realm: Realm) -> None:
    head, body, _ = realm.token().split(".")
    other = realm.token(sub="someone-else").split(".")[2]
    assert_401(client.get("/me", headers=bearer(f"{head}.{body}.{other[::-1]}")))


def test_e0_13_t04_cross_tenant_404(client: TestClient, realm: Realm) -> None:
    resp = client.get("/tenants-demo/tenant-b", headers=bearer(realm.token()))
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"


def test_e0_13_t04_own_tenant_200(client: TestClient, realm: Realm) -> None:
    resp = client.get("/tenants-demo/tenant-a", headers=bearer(realm.token()))
    assert resp.status_code == 200


def test_super_admin_without_tenant_claim(client: TestClient, realm: Realm) -> None:
    token = realm.token(tenant_id=None, realm_access={"roles": ["super-admin"]})
    me = client.get("/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["tenant_id"] is None
    assert client.get("/tenants-demo/tenant-a", headers=bearer(token)).status_code == 404


def test_user_in_two_orgs_without_selection_has_no_tenant(client: TestClient, realm: Realm) -> None:
    token = realm.token(tenant_id=["tenant-a", "tenant-b"])
    assert client.get("/me", headers=bearer(token)).json()["tenant_id"] is None
    assert client.get("/tenants-demo/tenant-a", headers=bearer(token)).status_code == 404


def test_e0_13_t05_key_rotation_without_restart(client: TestClient, realm: Realm) -> None:
    assert client.get("/me", headers=bearer(realm.token())).status_code == 200
    realm.rotate("kid-2")
    assert client.get("/me", headers=bearer(realm.token())).status_code == 200
    assert realm.fetches == 2  # unknown kid triggered exactly one refetch


def test_e0_13_t07_wrong_audience_401(client: TestClient, realm: Realm) -> None:
    assert_401(client.get("/me", headers=bearer(realm.token(aud="spike-other"))))
    assert_401(client.get("/me", headers=bearer(realm.token(aud=None))))


def test_wrong_issuer_401(client: TestClient, realm: Realm) -> None:
    assert_401(client.get("/me", headers=bearer(realm.token(iss="http://evil.test/realms/x"))))


def test_e0_13_t08_keycloak_down_cached_keys_still_verify(client: TestClient, realm: Realm) -> None:
    token = realm.token()
    assert client.get("/me", headers=bearer(token)).status_code == 200
    realm.down = True
    assert client.get("/me", headers=bearer(token)).status_code == 200
    realm.rotate("kid-new")  # a key the API never saw, while Keycloak is down
    assert_401(client.get("/me", headers=bearer(realm.token())))
