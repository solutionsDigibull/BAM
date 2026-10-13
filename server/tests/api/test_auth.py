"""E2.2 authentication + authorization chain, in-process: in-memory RSA keys and a fake JWKS.

Test routes use the real policies (`admin`, `fde`, `super_admin`, `project_access`, `locked`).
The JWKS and the ports (memberships, audit) are fakes; Keycloak itself is in the integration tests.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sbam_api.core.auth import (
    Auth,
    CurrentUser,
    Jwks,
    TokenVerifier,
    admin,
    current_user,
    fde,
    project_access,
    super_admin,
)
from sbam_api.core.conventions import locked
from sbam_api.core.ids import uuid7
from sbam_api.main import create_app
from sbam_contracts.api import API_BASE
from test_core_app import ALL_OK, log_lines, make_settings

ISSUER = "http://kc.test/realms/bullm"
AUDIENCE = "sbam-api"
TENANT_A, TENANT_B = uuid.uuid4(), uuid.uuid4()
FDE_A1, FDE_A2, FDE_B1, ADMIN_A = (uuid.uuid4() for _ in range(4))
PROJECT_A1, PROJECT_B1 = uuid7(), uuid7()


class Realm:
    """Signing keys plus the JWKS the fake client serves; `down` = Keycloak stopped."""

    def __init__(self) -> None:
        self.keys: dict[str, rsa.RSAPrivateKey] = {}
        self.down = False
        self.fetches = 0
        self.rotate("kid-1")

    def rotate(self, kid: str, bits: int = 2048) -> None:
        self.keys = {kid: rsa.generate_private_key(public_exponent=65537, key_size=bits)}
        self.kid = kid

    def jwks(self) -> dict[str, Any]:
        out = []
        for kid, key in self.keys.items():
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
            out.append({**jwk, "kid": kid, "use": "sig", "alg": "RS256"})
        return {"keys": out}

    def token(self, sub: uuid.UUID = FDE_A1, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(sub),
            "iat": now,
            "exp": now + 300,
            "realm_access": {"roles": ["sbam-fde"]},
            "tenant_id": [str(TENANT_A)],
            **overrides,
        }
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.keys[self.kid], "RS256", headers={"kid": self.kid})


class FakeJwks(Jwks):
    def __init__(self, realm: Realm) -> None:
        super().__init__("http://kc.test/certs")
        self.realm = realm

    def fetch_data(self) -> Any:
        self.realm.fetches += 1
        if self.realm.down:
            raise jwt.PyJWKClientConnectionError("connection refused")
        data = self.realm.jwks()
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data


class Memberships:
    def __init__(self) -> None:
        self.assigned = {(TENANT_A, FDE_A1): frozenset({PROJECT_A1})}
        self.calls = 0

    def assigned_projects(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> frozenset[uuid.UUID]:
        self.calls += 1
        return self.assigned.get((tenant_id, user_id), frozenset())


class Audit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def denied(self, *, actor: CurrentUser, route: str, target: str | None, reason: str) -> None:
        self.entries.append({"actor": actor.keycloak_sub, "route": route, "target": target})

    def event(
        self,
        *,
        actor: str,
        tenant_id: uuid.UUID | None,
        action: str,
        entity: str,
        entity_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.entries.append({"actor": actor, "action": action})


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.realm = Realm()
        self.jwks = FakeJwks(self.realm)
        self.memberships = Memberships()
        self.audit = Audit()
        self.side_effects: list[str] = []
        self.handled = 0
        auth = Auth(TokenVerifier(ISSUER, AUDIENCE, self.jwks), self.memberships, self.audit)
        app = create_app(make_settings(tmp_path), ALL_OK, auth)
        app.include_router(self.routes(), prefix=API_BASE)
        self.client = TestClient(app)

    def routes(self) -> APIRouter:
        router = APIRouter()
        project = Annotated[uuid.UUID, Depends(project_access)]

        @router.get("/projects/{project_id}/pipeline")
        def pipeline(pid: project) -> dict[str, str]:
            self.handled += 1
            return {"project_id": str(pid), "state": "IDLE"}

        @router.post("/projects/{project_id}/pipeline/start")
        def start(pid: project) -> dict[str, str]:
            self.side_effects.append(f"started {pid}")
            return {"started": str(pid)}

        @router.put("/projects/{project_id}/workflows/frozen")
        def edit_frozen(pid: project) -> None:
            raise locked()  # the state guard runs after authorization

        @router.get("/admin/projects")
        def admin_projects(user: Annotated[CurrentUser, Depends(admin)]) -> dict[str, str]:
            return {"tenant_id": str(user.tenant_id)}

        @router.get("/admin/platform-check")
        def tenants(user: Annotated[CurrentUser, Depends(super_admin)]) -> list[str]:
            return []

        @router.get("/projects/{project_id}/whoami", dependencies=[Depends(fde)])
        def whoami(user: Annotated[CurrentUser, Depends(current_user)]) -> dict[str, Any]:
            return {"tenant_id": str(user.tenant_id), "roles": sorted(user.roles)}

        return router

    def get(self, path: str, token: str | None, **headers: str) -> Any:
        if token is not None:
            headers["authorization"] = f"Bearer {token}"
        return self.client.get(f"{API_BASE}{path}", headers=headers)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def problem(res: Any, status: int, code: str) -> dict[str, Any]:
    assert res.status_code == status, res.text
    body: dict[str, Any] = res.json()
    assert res.headers["content-type"].startswith("application/problem+json")
    assert body["code"] == code and body["status"] == status and body["correlation_id"]
    assert {"type", "title", "status", "code", "correlation_id"} <= body.keys()
    return body


def test_e2_2_t01_valid_fde_request_on_assigned_project(h: Harness) -> None:
    res = h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token())
    assert res.status_code == 200
    assert res.json() == {"project_id": str(PROJECT_A1), "state": "IDLE"}


def test_e2_2_t02_expired_token_is_401(h: Harness) -> None:
    now = int(time.time())
    res = h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token(exp=now - 60, iat=now - 400))
    problem(res, 401, "UNAUTHENTICATED")
    assert res.headers["www-authenticate"] == "Bearer"


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_e2_2_t03_forged_tokens_are_401_and_nothing_runs(h: Harness) -> None:
    good = h.realm.token(realm_access={"roles": ["sbam-admin", "sbam-fde"]})
    header, payload, _ = good.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{payload}."
    public_pem = (
        h.realm.keys[h.realm.kid]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    hs_header = _b64({"alg": "HS256", "typ": "JWT", "kid": h.realm.kid})
    hs_signing_input = f"{hs_header}.{_b64(claims)}"
    hs_sig = hmac.new(public_pem, hs_signing_input.encode(), hashlib.sha256).digest()
    hs256 = f"{hs_signing_input}.{base64.urlsafe_b64encode(hs_sig).rstrip(b'=').decode()}"
    tampered = f"{header}.{_b64({**claims, 'tenant_id': [str(TENANT_B)]})}.{good.split('.')[2]}"
    for token in (unsigned, hs256, tampered, "not-a-jwt"):
        problem(h.get(f"/projects/{PROJECT_A1}/pipeline", token), 401, "UNAUTHENTICATED")
        problem(h.get("/admin/projects", token), 401, "UNAUTHENTICATED")
    assert h.handled == 0 and h.memberships.calls == 0  # no handler, no membership (DB) query


def test_e2_2_missing_or_malformed_authorization_is_401(h: Harness) -> None:
    problem(h.get(f"/projects/{PROJECT_A1}/pipeline", None), 401, "UNAUTHENTICATED")
    res = h.client.get(
        f"{API_BASE}/projects/{PROJECT_A1}/pipeline", headers={"authorization": "Basic abc"}
    )
    problem(res, 401, "UNAUTHENTICATED")


def test_e2_2_t04_fde_on_admin_api_is_wrong_portal(h: Harness) -> None:
    problem(h.get("/admin/projects", h.realm.token()), 403, "WRONG_PORTAL")


def test_e2_2_t05_admin_on_fde_api_is_wrong_portal_and_nothing_starts(h: Harness) -> None:
    token = h.realm.token(ADMIN_A, realm_access={"roles": ["sbam-admin"]})
    res = h.client.post(
        f"{API_BASE}/projects/{PROJECT_A1}/pipeline/start",
        headers={"authorization": f"Bearer {token}"},
    )
    problem(res, 403, "WRONG_PORTAL")
    assert h.side_effects == []


def without_correlation(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != "correlation_id"}


def test_e2_2_t06_unassigned_project_is_404_identical_to_a_missing_one(h: Harness) -> None:
    token = h.realm.token(FDE_A2)  # same tenant, not a member of PROJECT_A1
    unassigned = problem(h.get(f"/projects/{PROJECT_A1}/pipeline", token), 404, "NOT_FOUND")
    missing = problem(h.get(f"/projects/{uuid7()}/pipeline", token), 404, "NOT_FOUND")
    assert without_correlation(unassigned) == without_correlation(missing)


def test_e2_2_t07_e0_7_t07_cross_tenant_is_404_and_audited(h: Harness) -> None:
    token = h.realm.token(FDE_B1, tenant_id=[str(TENANT_B)])
    h.memberships.assigned[(TENANT_B, FDE_B1)] = frozenset({PROJECT_B1})
    cross = problem(h.get(f"/projects/{PROJECT_A1}/pipeline", token), 404, "NOT_FOUND")
    random = problem(h.get(f"/projects/{uuid7()}/pipeline", token), 404, "NOT_FOUND")
    assert without_correlation(cross) == without_correlation(random)  # E0.7-T07
    entry = next(e for e in h.audit.entries if e["target"] == str(PROJECT_A1))
    assert entry["actor"] == str(FDE_B1)
    assert entry["route"] == f"{API_BASE}/projects/{PROJECT_A1}/pipeline"


def test_e2_2_malformed_project_id_is_404_not_422(h: Harness) -> None:
    for bad in ("not-a-uuid", str(uuid.uuid4())):  # garbage, and a UUID that isn't v7
        problem(h.get(f"/projects/{bad}/pipeline", h.realm.token()), 404, "NOT_FOUND")


def test_e2_2_tenant_comes_only_from_the_token(h: Harness) -> None:
    res = h.client.get(
        f"{API_BASE}/projects/{PROJECT_A1}/whoami?tenant_id={TENANT_B}",
        headers={"authorization": f"Bearer {h.realm.token()}", "x-tenant-id": str(TENANT_B)},
    )
    assert res.json()["tenant_id"] == str(TENANT_A)


@pytest.mark.parametrize("tenant_claim", [None, [], [str(TENANT_A), str(TENANT_B)], ["tenant-a"]])
def test_e2_2_no_single_tenant_uuid_means_404_on_tenant_routes(
    h: Harness, tenant_claim: list[str] | None
) -> None:
    token = h.realm.token(tenant_id=tenant_claim)
    problem(h.get(f"/projects/{PROJECT_A1}/pipeline", token), 404, "NOT_FOUND")
    admin_token = h.realm.token(
        ADMIN_A, tenant_id=tenant_claim, realm_access={"roles": ["sbam-admin"]}
    )
    problem(h.get("/admin/projects", admin_token), 404, "NOT_FOUND")


def test_e2_2_user_with_both_roles_uses_both_portals(h: Harness) -> None:
    token = h.realm.token(realm_access={"roles": ["sbam-admin", "sbam-fde"]})
    assert h.get("/admin/projects", token).status_code == 200
    assert h.get(f"/projects/{PROJECT_A1}/pipeline", token).status_code == 200


def test_e2_2_super_admin_routes(h: Harness) -> None:
    root = h.realm.token(tenant_id=None, realm_access={"roles": ["super-admin"]})
    assert h.get("/admin/platform-check", root).status_code == 200
    admin_token = h.realm.token(ADMIN_A, realm_access={"roles": ["sbam-admin"]})
    problem(h.get("/admin/platform-check", admin_token), 403, "WRONG_PORTAL")  # E2.3-T04


def test_e2_2_state_guard_is_423_after_authorization(h: Harness) -> None:
    path = f"{API_BASE}/projects/{PROJECT_A1}/workflows/frozen"
    ok = h.client.put(path, headers={"authorization": f"Bearer {h.realm.token()}"})
    problem(ok, 423, "RESOURCE_LOCKED")
    other = h.client.put(path, headers={"authorization": f"Bearer {h.realm.token(FDE_A2)}"})
    problem(other, 404, "NOT_FOUND")  # authorization first: an outsider never learns it is frozen


def test_e2_2_wrong_issuer_audience_or_future_nbf_is_401(h: Harness) -> None:
    for overrides in (
        {"iss": "http://evil.test/realms/bullm"},
        {"aud": "another-api"},
        {"nbf": int(time.time()) + 120},
        {"sub": None},
        {"sub": "not-a-uuid"},
    ):
        problem(
            h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token(**overrides)),
            401,
            "UNAUTHENTICATED",
        )


def test_e2_2_clock_skew_within_30_s_is_accepted(h: Harness) -> None:
    now = int(time.time())
    token = h.realm.token(exp=now - 20, iat=now - 320)
    assert h.get(f"/projects/{PROJECT_A1}/pipeline", token).status_code == 200


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")  # on purpose
def test_e2_2_rsa_keys_under_2048_bits_are_refused(h: Harness) -> None:
    h.realm.rotate("weak", bits=1024)
    problem(h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token()), 401, "UNAUTHENTICATED")


def test_e2_2_key_rotation_and_unknown_kid_cooldown(h: Harness) -> None:
    assert h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token()).status_code == 200
    fetches = h.realm.fetches
    forged = Realm()
    forged.rotate("forged-1")
    for i in range(5):  # forged kids must not make us hammer Keycloak
        forged.rotate(f"forged-{i}")
        problem(h.get(f"/projects/{PROJECT_A1}/pipeline", forged.token()), 401, "UNAUTHENTICATED")
    assert h.realm.fetches - fetches <= 1


def test_e2_2_t08_keycloak_down_cached_keys_still_verify(h: Harness) -> None:
    assert h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token()).status_code == 200
    h.realm.down = True
    assert h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token()).status_code == 200
    h.jwks.jwk_set_cache = None  # the 10-minute cache has expired too: last good keys still work
    assert h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token()).status_code == 200


def test_e2_2_t09_keycloak_down_and_unknown_kid_is_503(h: Harness) -> None:
    assert h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token()).status_code == 200
    h.realm.down = True
    h.jwks.jwk_set_cache = None  # no cooldown shortcut: the client must try Keycloak
    h.realm.rotate("kid-2")  # Keycloak rotated before it went down; this kid was never seen
    problem(h.get(f"/projects/{PROJECT_A1}/pipeline", h.realm.token()), 503, "IDP_UNAVAILABLE")
    assert h.handled == 1


def test_e2_2_t10_route_without_a_policy_fails_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sbam_api.main as main

    router = APIRouter()

    @router.get("/unguarded")
    def unguarded() -> None: ...

    monkeypatch.setattr(main, "discover_routers", lambda: [router])
    with pytest.raises(RuntimeError, match="/api/v1/unguarded"):
        create_app(make_settings(tmp_path), ALL_OK)


def test_e2_2_tokens_never_reach_the_logs(h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
    from sbam_api.core.logging import configure_logging

    configure_logging()
    token = h.realm.token(FDE_B1, tenant_id=[str(TENANT_B)])
    h.get(f"/projects/{PROJECT_A1}/pipeline", token)  # denied: an audit/access log line is written
    out = capsys.readouterr().out
    assert token not in out and token.split(".")[2] not in out
    assert log_lines(out)
