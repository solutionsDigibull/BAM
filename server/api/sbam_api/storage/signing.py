"""Signed download URLs: HMAC-SHA256 over key + expiry + tenant, valid 1-900 s (E1.6).

A link can also carry the file name and content type the browser should use (#402). They are part
of what is signed, so they cannot be edited, and they are restricted to plain names and a short list
of types, so they cannot inject a header or make a file render in the page."""

import hashlib
import hmac
import re
import time
from urllib.parse import urlencode

from sbam_contracts.api import API_BASE, ErrorCode

from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import StorageUnavailable, parse_key

MAX_EXPIRY_SECONDS = 900
MIN_KEY_LENGTH = 32

# What a download may be called and served as. The names are letters, digits, dot, dash and
# underscore only (no path, space, quote or control character); the types are the ones SBAM issues.
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
DOWNLOAD_TYPES = frozenset(
    {
        "application/zip",  # the OKF bundle
        "application/pdf",  # SOW exports
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/markdown; charset=utf-8",
    }
)


def _check_name(filename: str | None, content_type: str | None) -> None:
    """Both or neither; a plain name and an allowed type (a programming error otherwise)."""
    if (filename is None) != (content_type is None):
        raise ValueError("a download needs both a file name and a content type, or neither")
    if filename is not None and not SAFE_FILENAME.fullmatch(filename):
        raise ValueError("the file name may only hold letters, digits, '.', '-' and '_'")
    if content_type is not None and content_type not in DOWNLOAD_TYPES:
        raise ValueError(f"content type not allowed for a download: {content_type!r}")


def _signature(
    secret: str,
    key: str,
    expires: int,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    tenant, _ = parse_key(key)
    message = f"{key}\n{expires}\n{tenant}"
    if filename is not None:  # a link without a name is signed exactly as it always was
        message += f"\n{filename}\n{content_type}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _require(secret: str | None) -> str:
    # Never sign with a guessable key: empty, or short like the `change-me-storage` placeholder.
    if not secret or len(secret) < MIN_KEY_LENGTH:
        raise StorageUnavailable()
    return secret


def signed_url(
    secret: str | None,
    key: str,
    ctx: TenantContext,
    expires_in: int,
    now: float | None = None,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """A download URL for `key`, only for a caller in the key's tenant (else the usual 404).

    `filename` and `content_type` (both or neither) are what the browser saves and serves the file
    as; without them it is a plain attachment named after the key."""
    tenant, _ = parse_key(key)
    if tenant != ctx.tenant_id:
        raise not_found()
    if not 1 <= expires_in <= MAX_EXPIRY_SECONDS:
        raise ValueError(f"expires_in must be 1-{MAX_EXPIRY_SECONDS} s")
    _check_name(filename, content_type)
    expires = int(now if now is not None else time.time()) + expires_in
    sig = _signature(_require(secret), key, expires, filename, content_type)
    query = {"exp": expires, "sig": sig}
    if filename is not None and content_type is not None:
        query |= {"name": filename, "type": content_type}
    return f"{API_BASE}/files/{key}?{urlencode(query)}"


def verify(
    secret: str | None,
    key: str,
    expires: int,
    sig: str,
    now: float | None = None,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> None:
    """403 for an expired, tampered or foreign signature; the same answer for all three. A name or
    type that was added, removed or edited is a tampered signature."""
    try:
        _check_name(filename, content_type)
    except ValueError:
        raise ApiError(
            403, ErrorCode.FORBIDDEN, "Download link is invalid or has expired"
        ) from None
    expected = _signature(_require(secret), key, expires, filename, content_type)
    current = now if now is not None else time.time()
    if expires < current or not hmac.compare_digest(expected, sig):
        raise ApiError(403, ErrorCode.FORBIDDEN, "Download link is invalid or has expired")
