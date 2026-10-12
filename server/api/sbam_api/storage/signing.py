"""Signed download URLs: HMAC-SHA256 over key + expiry + tenant, valid 1-900 s (E1.6)."""

import hashlib
import hmac
import time
from urllib.parse import urlencode

from sbam_contracts.api import API_BASE, ErrorCode

from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import StorageUnavailable, parse_key

MAX_EXPIRY_SECONDS = 900
MIN_KEY_LENGTH = 32


def _signature(secret: str, key: str, expires: int) -> str:
    tenant, _ = parse_key(key)
    message = f"{key}\n{expires}\n{tenant}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _require(secret: str | None) -> str:
    # Never sign with a guessable key: empty, or short like the `change-me-storage` placeholder.
    if not secret or len(secret) < MIN_KEY_LENGTH:
        raise StorageUnavailable()
    return secret


def signed_url(
    secret: str | None, key: str, ctx: TenantContext, expires_in: int, now: float | None = None
) -> str:
    """A download URL for `key`, only for a caller in the key's tenant (else the usual 404)."""
    tenant, _ = parse_key(key)
    if tenant != ctx.tenant_id:
        raise not_found()
    if not 1 <= expires_in <= MAX_EXPIRY_SECONDS:
        raise ValueError(f"expires_in must be 1-{MAX_EXPIRY_SECONDS} s")
    expires = int(now if now is not None else time.time()) + expires_in
    sig = _signature(_require(secret), key, expires)
    return f"{API_BASE}/files/{key}?{urlencode({'exp': expires, 'sig': sig})}"


def verify(secret: str | None, key: str, expires: int, sig: str, now: float | None = None) -> None:
    """403 for an expired, tampered or foreign signature; the same answer for all three."""
    expected = _signature(_require(secret), key, expires)
    current = now if now is not None else time.time()
    if expires < current or not hmac.compare_digest(expected, sig):
        raise ApiError(403, ErrorCode.FORBIDDEN, "Download link is invalid or has expired")
