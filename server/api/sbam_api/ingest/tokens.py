"""The upload token: signed, for one project and one user, valid for at most 15 minutes (E4.2).

`<payload>.<signature>`, both base64url. The signing key is derived from the tusd hook secret, so
there is no second secret to configure. The token proves who asked and for which project; which
files are allowed is decided by the upload reservations the token's `batch` points to.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

_CONTEXT = b"sbam-upload-token-v1"


class TokenError(ValueError):
    """Malformed, forged or expired. The reason is for logs only, never for the caller."""


@dataclass(frozen=True)
class UploadClaims:
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    batch_id: uuid.UUID
    expires_at: int  # unix seconds


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: str) -> str:
    key = hmac.new(secret.encode(), _CONTEXT, hashlib.sha256).digest()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def issue(secret: str, claims: UploadClaims) -> str:
    body = {
        "t": str(claims.tenant_id),
        "p": str(claims.project_id),
        "u": str(claims.user_id),
        "b": str(claims.batch_id),
        "e": claims.expires_at,
    }
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload}.{_sign(secret, payload)}"


def verify(
    secret: str, token: str, now: float | None = None, *, check_expiry: bool = True
) -> UploadClaims:
    """`check_expiry=False` for post-finish: a long upload may finish after its token expired."""
    payload, dot, signature = token.partition(".")
    if not dot or not hmac.compare_digest(signature, _sign(secret, payload)):
        raise TokenError("bad signature")
    try:
        body = json.loads(_unb64(payload))
        claims = UploadClaims(
            tenant_id=uuid.UUID(body["t"]),
            project_id=uuid.UUID(body["p"]),
            user_id=uuid.UUID(body["u"]),
            batch_id=uuid.UUID(body["b"]),
            expires_at=int(body["e"]),
        )
    except (ValueError, KeyError, TypeError):
        raise TokenError("bad payload") from None
    if check_expiry and (time.time() if now is None else now) >= claims.expires_at:
        raise TokenError("expired")
    return claims
