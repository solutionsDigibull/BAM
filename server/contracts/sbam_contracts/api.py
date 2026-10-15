"""API conventions shared by every SBAM endpoint (TD §8.1, docs/api-conventions.md).

Errors are RFC 9457 problem+json bodies (`ProblemDetail`) carrying a stable `ErrorCode`.
"""

import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROBLEM_JSON = "application/problem+json"
API_BASE = "/api/v1"
PAGE_LIMIT_DEFAULT = 25
PAGE_LIMIT_MAX = 100


class ErrorCode(StrEnum):
    """Stable machine-readable error codes. Add new codes here; never rename or reuse one."""

    # Generic, one per status family
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    WRONG_PORTAL = "WRONG_PORTAL"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    PRECONDITION_REQUIRED = "PRECONDITION_REQUIRED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    RESOURCE_LOCKED = "RESOURCE_LOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # Conventions
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    INVALID_CURSOR = "INVALID_CURSOR"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    READ_ONLY = "READ_ONLY"
    GONE = "GONE"  # 410: permanently removed (a purged document cannot be restored)
    # Identity and tenancy
    TENANT_MISMATCH = "TENANT_MISMATCH"
    TENANT_SUSPENDED = "TENANT_SUSPENDED"
    IDP_UNAVAILABLE = "IDP_UNAVAILABLE"
    LAST_ADMIN = "LAST_ADMIN"
    USER_EXISTS = "USER_EXISTS"
    # Projects and masters
    DUPLICATE_NAME = "DUPLICATE_NAME"
    PROJECT_ARCHIVED = "PROJECT_ARCHIVED"
    DRAFT_EXISTS = "DRAFT_EXISTS"
    STRUCTURE_MISMATCH = "STRUCTURE_MISMATCH"
    FILETYPE_FROZEN = "FILETYPE_FROZEN"
    SECRET_VALUE_NOT_ALLOWED = "SECRET_VALUE_NOT_ALLOWED"  # noqa: S105 - error code, not a secret
    # Documents and ingestion
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_LIMIT_REACHED = "FILE_LIMIT_REACHED"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    INVALID_ENCODING = "INVALID_ENCODING"
    NESTED_ARCHIVE = "NESTED_ARCHIVE"
    ARCHIVE_LIMIT = "ARCHIVE_LIMIT"
    HASH_MISMATCH = "HASH_MISMATCH"
    NO_DOCUMENTS = "NO_DOCUMENTS"
    PARSE_ERROR = "PARSE_ERROR"
    SCAN_UNAVAILABLE = "SCAN_UNAVAILABLE"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    MALWARE = "MALWARE"  # a document version rejected because the scan found malware
    # Knowledge
    KNOWLEDGE_BUILDING = "KNOWLEDGE_BUILDING"
    KNOWLEDGE_NOT_READY = "KNOWLEDGE_NOT_READY"
    COGNEE_ACL_DISABLED = "COGNEE_ACL_DISABLED"
    # SOW
    SOW_STALE = "SOW_STALE"
    SOW_LOCKED = "SOW_LOCKED"
    SOW_APPROVED = "SOW_APPROVED"
    SOW_NOT_APPROVED = "SOW_NOT_APPROVED"
    SOW_NOT_LATEST = "SOW_NOT_LATEST"
    SOW_VERSION_CONFLICT = "SOW_VERSION_CONFLICT"
    NO_APPROVED_SOW = "NO_APPROVED_SOW"
    NOT_IN_REVIEW = "NOT_IN_REVIEW"
    # Pipeline and orchestration
    PIPELINE_RUNNING = "PIPELINE_RUNNING"
    PIPELINE_NOT_WAITING = "PIPELINE_NOT_WAITING"
    TEMPORAL_UNAVAILABLE = "TEMPORAL_UNAVAILABLE"
    ORCHESTRATOR_UNAVAILABLE = "ORCHESTRATOR_UNAVAILABLE"
    # ISATVON, manifests and build
    ISATVON_CONTRACT = "ISATVON_CONTRACT"
    SKILL_ID_PREFIX_MISMATCH = "SKILL_ID_PREFIX_MISMATCH"
    MACRO_REQUIRES_SUB_SKILL = "MACRO_REQUIRES_SUB_SKILL"
    GATE_PARSE_ERROR = "GATE_PARSE_ERROR"
    UNRESOLVED_IDENTIFIER = "UNRESOLVED_IDENTIFIER"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    SCHEMA_PARSE_ERROR = "SCHEMA_PARSE_ERROR"
    LIBRARY_NOT_IN_REGISTRY = "LIBRARY_NOT_IN_REGISTRY"
    TOOLS_NOT_DECOUPLED = "TOOLS_NOT_DECOUPLED"
    WORKFLOW_BUILDING = "WORKFLOW_BUILDING"
    # Tests and sandbox
    MIN_TEST_CASES = "MIN_TEST_CASES"
    MOCK_UNMATCHED = "MOCK_UNMATCHED"
    # Certification and versions
    WORKFLOW_FROZEN = "WORKFLOW_FROZEN"
    NOT_FROZEN = "NOT_FROZEN"


class FieldError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="Dotted path of the offending field, e.g. 'limit'")
    message: str = Field(min_length=1)


# ponytail: pattern screen, not a parser. It catches the usual leaks (tracebacks, SQL echoes,
# server paths, credentials); the real guard is never building `detail` from exceptions.
_UNSAFE_DETAIL = [
    re.compile(r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+"),
    re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*\b(FROM|INTO|SET|WHERE)\b"),
    re.compile(r"\b(psycopg|sqlalchemy|asyncpg)\b", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\|(?<![\w.])/(?:home|usr|var|etc|tmp|app|srv|opt|root)/"),
    re.compile(
        r"\bBearer\s+\S{8,}|\bsk-[A-Za-z0-9-]{16,}|\bpk_[A-Za-z0-9_]{16,}|\beyJ[\w-]{10,}\."
    ),
]


class ProblemDetail(BaseModel):
    """`application/problem+json` body returned for every 4xx/5xx response."""

    model_config = ConfigDict(extra="forbid")

    type: str = "about:blank"
    title: str = Field(min_length=1)
    status: int = Field(ge=400, le=599)
    detail: str | None = None
    code: ErrorCode
    errors: list[FieldError] = Field(default_factory=list)
    correlation_id: str = Field(min_length=1)

    @field_validator("detail")
    @classmethod
    def _no_internals(cls, value: str | None) -> str | None:
        if value is not None and any(p.search(value) for p in _UNSAFE_DETAIL):
            raise ValueError("detail must not expose stack traces, SQL, server paths or tokens")
        return value


Limit = Annotated[int, Field(ge=1, le=PAGE_LIMIT_MAX)]


class PageParams(BaseModel):
    """Query parameters for list endpoints: `?cursor=&limit=&sort=`."""

    model_config = ConfigDict(extra="forbid")

    limit: Limit = PAGE_LIMIT_DEFAULT
    cursor: str | None = None
    sort: str | None = None


class Page[T](BaseModel):
    """One page of a list response. `next_cursor` is null on the last page."""

    items: list[T]
    next_cursor: str | None = None
