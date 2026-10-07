"""Access-token validation against Keycloak's JWKS (spike E0.13).

Rules: RS256 only (`none`/HS* refused before any key lookup), iss = realm URL, aud contains the
API, exp in the future with a 60 s leeway. `tenant_id` comes from the Organization membership
mapper as a list of org aliases; exactly one means a tenant, anything else means no tenant.
"""

from dataclasses import dataclass
from typing import Any

import jwt

ALLOWED_ALGORITHMS = ["RS256"]
LEEWAY_SECONDS = 60


class InvalidTokenError(Exception):
    """Any reason to answer 401. The message is for logs, never for the client."""


@dataclass(frozen=True)
class Principal:
    subject: str
    username: str | None
    client_id: str | None
    tenant_id: str | None
    roles: frozenset[str]


class TokenVerifier:
    def __init__(self, issuer: str, audience: str, jwks: jwt.PyJWKClient) -> None:
        self.issuer = issuer
        self.audience = audience
        # ponytail: PyJWKClient caches keys and refetches once on an unknown kid, which covers key
        # rotation (T05) and Keycloak downtime with cached keys (T08). It does not rate-limit
        # unknown-kid refetches; #48 should add a cooldown so forged kids can't hammer Keycloak.
        self.jwks = jwks

    def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("malformed token") from exc
        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise InvalidTokenError(f"algorithm {header.get('alg')!r} not allowed")
        try:
            key = self.jwks.get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self.audience,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
            raise InvalidTokenError(type(exc).__name__) from exc
        return principal_from_claims(claims)


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    orgs = claims.get("tenant_id")
    tenant = orgs[0] if isinstance(orgs, list) and len(orgs) == 1 else None
    roles = claims.get("realm_access", {}).get("roles", [])
    return Principal(
        subject=claims["sub"],
        username=claims.get("preferred_username"),
        client_id=claims.get("azp"),
        tenant_id=tenant if isinstance(tenant, str) else None,
        roles=frozenset(r for r in roles if isinstance(r, str)),
    )
