"""API conventions as framework-free helpers (TD §8.1, docs/api-conventions.md).

Handlers raise `ApiError`; the FastAPI exception handler (#34, E1.3) renders it as a
`ProblemDetail` with the request's correlation id and `Content-Type: application/problem+json`.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sbam_contracts.api import (
    PAGE_LIMIT_DEFAULT,
    PAGE_LIMIT_MAX,
    ErrorCode,
    FieldError,
    ProblemDetail,
)


class ApiError(Exception):
    """An error response. Raise it from services and handlers.

    Never build `detail` from an exception message.
    """

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        title: str,
        detail: str | None = None,
        errors: list[FieldError] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}: {title}")
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors or []

    def to_problem(self, correlation_id: str) -> ProblemDetail:
        return ProblemDetail(
            title=self.title,
            status=self.status,
            detail=self.detail,
            code=self.code,
            errors=self.errors,
            correlation_id=correlation_id,
        )


def not_found() -> ApiError:
    """The one 404 for missing, other-tenant and unassigned resources, so existence never leaks."""
    return ApiError(404, ErrorCode.NOT_FOUND, "Not found")


def _invalid(path: str, message: str, code: ErrorCode = ErrorCode.VALIDATION_ERROR) -> ApiError:
    return ApiError(400, code, "Invalid request", errors=[FieldError(path=path, message=message)])


# --- Concurrency: ETag = row_version, writes need If-Match ---------------------------------


def etag(row_version: int) -> str:
    return f'"{row_version}"'


def check_if_match(if_match: str | None, row_version: int, *, frozen: bool = False) -> None:
    """Frozen wins (423 even with a correct ETag), then missing (428), then stale (412)."""
    if frozen:
        raise ApiError(
            423,
            ErrorCode.RESOURCE_LOCKED,
            "Locked",
            "This resource is frozen / approved — create a new version to change it.",
        )
    if not if_match:
        raise ApiError(
            428, ErrorCode.PRECONDITION_REQUIRED, "If-Match header required on this write"
        )
    if if_match.strip().removeprefix("W/") != etag(row_version):
        raise ApiError(
            412, ErrorCode.PRECONDITION_FAILED, "Resource changed since you loaded it; reload"
        )


# --- Idempotency-Key on Generate / Retry / Export -------------------------------------------

IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
_KEY = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[A-Za-z0-9_-]{8,64}"
)


def validate_idempotency_key(key: str | None) -> str:
    if key is None or not _KEY.fullmatch(key):
        raise _invalid("Idempotency-Key", "UUID or 8-64 characters of A-Z, a-z, 0-9, _ or -")
    return key


def body_hash(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class IdempotencyScope:
    tenant_id: str
    user_id: str
    endpoint: str


class IdempotencyStore(Protocol):
    """Remembers (scope, key) → (body hash, run id) for 24 h. DB-backed store arrives with #34."""

    def get(self, scope: IdempotencyScope, key: str) -> tuple[str, str] | None: ...

    def put(self, scope: IdempotencyScope, key: str, digest: str, run_id: str) -> None: ...


@dataclass
class InMemoryIdempotencyStore:
    """Reference store for tests and local dev; single process only."""

    now: Any = time.monotonic
    _rows: dict[tuple[IdempotencyScope, str], tuple[str, str, float]] = field(default_factory=dict)

    def get(self, scope: IdempotencyScope, key: str) -> tuple[str, str] | None:
        row = self._rows.get((scope, key))
        if row is None or self.now() - row[2] > IDEMPOTENCY_TTL_SECONDS:
            return None
        return row[0], row[1]

    def put(self, scope: IdempotencyScope, key: str, digest: str, run_id: str) -> None:
        self._rows[(scope, key)] = (digest, run_id, self.now())


def replay_run_id(
    store: IdempotencyStore, scope: IdempotencyScope, key: str, body: Mapping[str, Any]
) -> str | None:
    """Run id to replay for a repeated request, None for a new one; 422 if the key is reused."""
    seen = store.get(scope, validate_idempotency_key(key))
    if seen is None:
        return None
    digest, run_id = seen
    if digest != body_hash(body):
        raise ApiError(
            422,
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "Idempotency-Key already used with a different request body",
        )
    return run_id


# --- Pagination, sorting ----------------------------------------------------------------------


def parse_limit(raw: str | int | None) -> int:
    if raw is None or raw == "":
        return PAGE_LIMIT_DEFAULT
    if isinstance(raw, str) and not re.fullmatch(r"-?\d+", raw.strip()):
        raise _invalid("limit", "must be an integer")
    value = int(raw)
    if not 1 <= value <= PAGE_LIMIT_MAX:
        raise _invalid("limit", f"must be between 1 and {PAGE_LIMIT_MAX}")
    return value


def parse_sort(raw: str | None, allowed: frozenset[str]) -> tuple[str, bool] | None:
    """`-created_at` → ("created_at", descending=True). Only fields in `allowed` are accepted."""
    if not raw:
        return None
    name, desc = (raw[1:], True) if raw.startswith("-") else (raw, False)
    if name not in allowed:
        raise _invalid("sort", f"must be one of {', '.join(sorted(allowed))}")
    return name, desc


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode_cursor(position: Mapping[str, Any], tenant_id: str, secret: bytes) -> str:
    """Opaque cursor: payload + HMAC-SHA256 over tenant and payload.

    It can't be forged, edited or replayed against another tenant.
    """
    payload = json.dumps(position, sort_keys=True, separators=(",", ":")).encode()
    mac = hmac.new(secret, tenant_id.encode() + b"\x00" + payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(mac)}"


def decode_cursor(token: str, tenant_id: str, secret: bytes) -> dict[str, Any]:
    bad = _invalid("cursor", "invalid or expired cursor", ErrorCode.INVALID_CURSOR)
    try:
        payload_b64, mac_b64 = token.split(".")
        payload, mac = _unb64(payload_b64), _unb64(mac_b64)
    except (ValueError, binascii.Error):
        raise bad from None
    expected = hmac.new(secret, tenant_id.encode() + b"\x00" + payload, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise bad
    position = json.loads(payload)
    if not isinstance(position, dict):
        raise bad
    return position
