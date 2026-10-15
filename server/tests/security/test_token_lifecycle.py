"""E2.7-T06 token lifecycle through the real auth chain: expired, refreshed, revoked, forged (#53).

Keycloak owns refresh and logout; the API verifies access tokens statelessly (RS256 signature, exp,
iss, aud) and re-checks the user and tenant in its own database on every request. These tests prove
that half in-process. The Keycloak half (logout then refresh -> 400 invalid_grant) needs the
Compose stack: tests/integration/test_token_lifecycle_keycloak.py.
"""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx2 import Response
from rbac_world import AUDIENCE, ISSUER, World

PROTECTED = ("GET", "/projects")


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    w = World(tmp_path)
    yield w
    w.client.close()


def use(world: World, token: str | None, *, role: str = "fde_a_assigned") -> Response:
    method, path = PROTECTED
    return world.call(method, path, role, token=token or "")


def rejected(res: Response) -> None:
    assert res.status_code == 401, res.text
    assert res.json()["code"] == "UNAUTHENTICATED"
    assert res.headers["www-authenticate"].startswith("Bearer")


def claims(world: World, role: str, **overrides: Any) -> dict[str, Any]:
    actor = world.actors[role]
    now = int(time.time())
    base: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(actor.id),
        "iat": now,
        "exp": now + 300,
        "realm_access": {"roles": actor.roles},
        "tenant_id": [str(actor.tenant)],
    }
    return {**base, **overrides}


def sign(world: World, body: dict[str, Any], key: Any = None, alg: str = "RS256") -> str:
    return jwt.encode(body, key or world.realm.key, alg, headers={"kid": world.realm.kid})


# --- expiry and refresh ---------------------------------------------------------------------


def test_e2_7_t06_expired_token_is_401(world: World) -> None:
    expired = world.token("fde_a_assigned", exp_in=-120)

    rejected(use(world, expired))


def test_e2_7_t06_token_expires_between_two_calls(world: World) -> None:
    """Valid now, then the same token past exp (clock moved past leeway): 401."""
    now = int(time.time())
    token = sign(world, claims(world, "fde_a_assigned", iat=now - 400, exp=now + 1))
    assert use(world, token).status_code == 200

    later = sign(world, claims(world, "fde_a_assigned", iat=now - 400, exp=now - 31))
    rejected(use(world, later))


def test_e2_7_t06_refreshed_token_works_and_the_old_one_lives_only_until_its_exp(
    world: World,
) -> None:
    old = world.token("fde_a_assigned", exp_in=-120)  # what the browser held before refresh
    new = world.token("fde_a_assigned")  # what the refresh grant returned

    assert use(world, new).status_code == 200
    rejected(use(world, old))


# --- revocation: the API rechecks the user and tenant in its own database -------------------


def deactivate(world: World, role: str) -> None:
    etag = world.value(f"etag_user_{role}")
    res = world.call(
        "PATCH",
        f"/admin/users/{world.actors[role].id}",
        "admin_a",
        headers={"if-match": etag},
        json={"active": False},
    )
    assert res.status_code == 200, res.text


def test_e2_7_t06_revoked_user_loses_access_with_an_unexpired_token(world: World) -> None:
    token = world.token("fde_a_assigned")
    assert use(world, token).status_code == 200

    deactivate(world, "fde_a_assigned")

    rejected(use(world, token))
    sub = str(world.actors["fde_a_assigned"].id)
    # Keycloak is told to disable the account and end its sessions, so refresh dies there too
    assert ("set_enabled", sub) in world.keycloak.calls
    assert ("logout", sub) in world.keycloak.calls


def test_e2_7_t06_suspended_tenant_blocks_every_user_of_it(world: World) -> None:
    etag = world.value("etag_tenant_a")
    res = world.call(
        "PATCH",
        f"/admin/tenants/{world.tenant_a}",
        "super_admin",
        headers={"if-match": etag},
        json={"status": "SUSPENDED"},
    )
    assert res.status_code == 200, res.text

    for role in ("fde_a_assigned", "admin_a"):
        blocked = world.call("GET", "/me", role)
        assert blocked.status_code == 403, role
        assert blocked.json()["code"] == "TENANT_SUSPENDED"
    assert world.call("GET", "/me", "fde_b").status_code == 200


# --- forged and malformed tokens ------------------------------------------------------------


def test_e2_7_t06_token_signed_by_another_key_is_401(world: World) -> None:
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    rejected(use(world, sign(world, claims(world, "fde_a_assigned"), key=attacker)))


def test_e2_7_t06_tampered_payload_is_401(world: World) -> None:
    token = world.token("fde_a_assigned")
    assert token
    head, _, sig = token.split(".")
    forged_payload = jwt.utils.base64url_encode(
        b'{"sub":"%s"}' % str(world.actors["admin_a"].id).encode()
    ).decode()

    rejected(use(world, f"{head}.{forged_payload}.{sig}"))


def test_e2_7_t06_unsigned_alg_none_token_is_401(world: World) -> None:
    header = {"alg": "none", "typ": "JWT", "kid": world.realm.kid}
    none_token = (
        ".".join(
            jwt.utils.base64url_encode(json.dumps(part).encode()).decode()
            for part in (header, claims(world, "admin_a"))
        )
        + "."
    )

    rejected(use(world, none_token))


def test_e2_7_t06_symmetric_alg_confusion_is_401(world: World) -> None:
    """HS256 signed with the public key as the HMAC secret (PyJWT refuses to, so by hand)."""
    pem = world.realm.key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    header = {"alg": "HS256", "typ": "JWT", "kid": world.realm.kid}
    signing_input = ".".join(
        jwt.utils.base64url_encode(json.dumps(part).encode()).decode()
        for part in (header, claims(world, "admin_a"))
    )
    mac = hmac.new(pem, signing_input.encode(), hashlib.sha256).digest()
    forged = f"{signing_input}.{jwt.utils.base64url_encode(mac).decode()}"

    rejected(use(world, forged))


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "http://evil.test/realms/bullm"},
        {"aud": "some-other-client"},
        {"exp": None},
        {"sub": None},
    ],
    ids=["wrong-issuer", "wrong-audience", "no-exp", "no-sub"],
)
def test_e2_7_t06_token_failing_a_required_claim_is_401(
    world: World, overrides: dict[str, Any]
) -> None:
    body = {k: v for k, v in claims(world, "fde_a_assigned", **overrides).items() if v is not None}

    rejected(use(world, sign(world, body)))


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "Bearer"], ids=repr)
def test_e2_7_t06_garbage_bearer_is_401(world: World, token: str) -> None:
    rejected(world.call("GET", "/projects", "anon", headers={"authorization": f"Bearer {token}"}))


def test_e2_7_t06_token_with_two_tenant_claims_has_no_tenant_and_reaches_nothing(
    world: World,
) -> None:
    other = claims(world, "fde_a_assigned", tenant_id=[str(world.tenant_a), str(world.tenant_b)])

    res = use(world, sign(world, other))

    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"
    assert str(world.project_a) not in res.text


def test_e2_7_t06_valid_signature_with_another_tenant_claim_is_tenant_mismatch(
    world: World,
) -> None:
    """The signature is genuine but the claim says tenant B: the user row belongs to A."""
    swapped = claims(world, "fde_a_assigned", tenant_id=[str(world.tenant_b)])

    res = use(world, sign(world, swapped))

    assert res.status_code == 403
    assert res.json()["code"] == "TENANT_MISMATCH"
    assert str(world.project_a) not in res.text


def test_e2_7_t06_unknown_subject_is_mirrored_on_first_login_with_no_project_access(
    world: World,
) -> None:
    stranger = claims(world, "fde_a_assigned", sub=str(uuid.uuid4()))

    res = use(world, sign(world, stranger))

    assert res.status_code == 200
    assert res.json()["items"] == []
