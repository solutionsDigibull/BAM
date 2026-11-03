"""N-02b (#609): the JWKS fetch is behind a bulkhead, so a hung Keycloak cannot hold every request
thread on a cold cache or an unknown `kid`."""

import json
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sbam_api.core.auth import Jwks
from sbam_api.core.bulkhead import KEYCLOAK_JWKS
from sbam_api.core.conventions import ApiError

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
JWK.update({"kid": "known", "use": "sig", "alg": "RS256"})


def token(kid: str) -> str:
    return jwt.encode({"sub": "u"}, KEY, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def saturated() -> Iterator[None]:
    """Every slot of the JWKS bulkhead is taken by a (hung) fetch."""
    with ExitStack() as stack:
        for _ in range(KEYCLOAK_JWKS.limit):
            stack.enter_context(KEYCLOAK_JWKS.slot())
        yield


def test_n02b_the_jwks_bulkhead_is_registered_for_the_health_report() -> None:
    from sbam_api.core.bulkhead import REGISTRY

    assert KEYCLOAK_JWKS in REGISTRY and KEYCLOAK_JWKS.limit < 40


def test_n02b_a_full_bulkhead_reads_as_keycloak_being_down(saturated: None) -> None:
    jwks = Jwks("http://keycloak.invalid/certs")
    with pytest.raises(jwt.PyJWKClientConnectionError):
        jwks.fetch_data()


def test_n02b_an_unknown_kid_gets_a_503_when_the_bulkhead_is_full(saturated: None) -> None:
    jwks = Jwks("http://keycloak.invalid/certs")
    with pytest.raises(ApiError) as err:
        jwks.key_for(token("never-seen"))
    assert err.value.status == 503


def test_n02b_a_known_kid_still_verifies_when_the_bulkhead_is_full(saturated: None) -> None:
    jwks = Jwks("http://keycloak.invalid/certs")
    jwks.last_keys = [jwt.PyJWK.from_dict(JWK)]
    assert jwks.key_for(token("known")).key_id == "known"


def test_n02b_a_fetch_with_a_free_slot_goes_through_and_gives_the_slot_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": [JWK]})
    jwks = Jwks("http://keycloak.invalid/certs")
    assert jwks.fetch_data() == {"keys": [JWK]}
    assert KEYCLOAK_JWKS.in_use == 0
