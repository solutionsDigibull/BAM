"""Project connections: the keys and settings a workflow's services need (ADR 0030, issue #764).

A connection is a named environment variable of one project: its purpose, whether it is required,
whether a value is stored, and whether the last Test connection passed. **No response model here
has a field that can hold a value**: a stored value is write-only, and the only thing that ever
leaves the API about it is `masked_tail` (the last 2 or 4 characters, nothing for a short secret).
A contract test walks the generated schema of every response and fails on a `value`, `secret` or
`ciphertext` property; the one request that carries a value (`ConnectionValueIn`) holds it as
`SecretStr`.

`status` is derived: MISSING (no value), SET (a value not tested yet in this version), TESTED (the
last test of this value passed, or the FDE vouched that there is no service to test) and
TEST_FAILED.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Final
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.isatvon14 import SECRET_VALUE

NAME_PATTERN: Final = r"^[A-Z_][A-Z0-9_]{0,127}$"  # an environment variable name
MAX_VALUE_BYTES: Final = 8 * 1024
MAX_URL_CHARS: Final = 2048
MAX_TEXT_CHARS: Final = 500
MAX_TAIL_CHARS: Final = 4
MAX_BLOCKERS: Final = 500

Name = Annotated[str, Field(strict=True, pattern=NAME_PATTERN)]
Note = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=MAX_TEXT_CHARS), Field(strict=True)
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectionSource(StrEnum):
    ISATVON = "ISATVON"  # named in a Sub-Skill's V block (always required)
    REPO = "REPO"  # found in a repository's own `.env.example` (optional; WG-7, not built yet)
    FDE = "FDE"  # added by the FDE


class ConnectionStatus(StrEnum):
    MISSING = "MISSING"
    SET = "SET"
    TESTED = "TESTED"
    TEST_FAILED = "TEST_FAILED"


class ConnectionTestMode(StrEnum):
    HTTP = "HTTP"  # a real call to the service the FDE names
    NONE = "NONE"  # the FDE vouches there is no service to test; set only by an explicit action


class AuthScheme(StrEnum):
    BEARER = "BEARER"  # `Authorization: Bearer <value>`
    BASIC = "BASIC"  # `Authorization: Basic <value>`, the credential as the service wants it
    HEADER = "HEADER"  # `<auth_name>: <value>`
    QUERY = "QUERY"  # `?<auth_name>=<value>`


class ProbeMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"


class ConnectionTestOutcome(StrEnum):
    OK = "OK"
    UNAUTHORIZED = "UNAUTHORIZED"  # HTTP 401 or 403: the service rejected this key
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    REDIRECT = "REDIRECT"  # never followed
    UNREACHABLE = "UNREACHABLE"
    TIMEOUT = "TIMEOUT"
    TLS_ERROR = "TLS_ERROR"
    BLOCKED_HOST = "BLOCKED_HOST"  # the SSRF guard refused the address


class BlockerReason(StrEnum):
    MISSING = "MISSING"  # a required name has no value
    NOT_TESTED = "NOT_TESTED"  # a value that has not been tested (or changed since)
    TEST_FAILED = "TEST_FAILED"  # the last test of the value failed
    INVALID_NAME = "INVALID_NAME"  # the ISATVON names something that cannot be an env variable


class CleanStep(StrEnum):
    TRIMMED = "TRIMMED"
    UNQUOTED = "UNQUOTED"


class ValueRejection(StrEnum):
    """Why a value was refused before anything was stored."""

    EMPTY_VALUE = "EMPTY_VALUE"
    NUL_BYTE = "NUL_BYTE"
    TOO_LARGE = "TOO_LARGE"


class ConnectionTestConfig(_Strict):
    """Where and how to test a key. No value in here; a change makes the stored value untested."""

    url: Annotated[str, Field(strict=True, min_length=9, max_length=MAX_URL_CHARS)]
    method: ProbeMethod = ProbeMethod.GET
    auth: AuthScheme = AuthScheme.BEARER
    auth_name: Annotated[str | None, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")] = (
        None  # the header or query parameter name (HEADER, QUERY)
    )

    @field_validator("url")
    @classmethod
    def _https_without_credentials(cls, url: str) -> str:
        try:
            parts = urlsplit(url)
            parts.port  # noqa: B018 - raises for a bad port
        except ValueError:
            raise ValueError("not a URL") from None
        if parts.scheme != "https":
            raise ValueError("only https")
        if not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError("a host and no credentials")
        if "@" in parts.netloc or parts.fragment or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError("not a plain URL")
        return url

    @model_validator(mode="after")
    def _name_for_header_and_query(self) -> "ConnectionTestConfig":
        needs_name = self.auth in (AuthScheme.HEADER, AuthScheme.QUERY)
        if needs_name and self.auth_name is None:
            raise ValueError("this auth scheme needs a name")
        if not needs_name and self.auth_name is not None:
            raise ValueError("this auth scheme takes no name")
        return self


# --- requests -------------------------------------------------------------------------------------


class ConnectionValueIn(_Strict):
    """`PUT .../connections/{name}/value`. The only model in this module that carries a value."""

    value: Annotated[SecretStr, Field(json_schema_extra={"writeOnly": True})]


class ConnectionUpdate(_Strict):
    """`PUT .../connections/{name}`: an entry's metadata. Any field left out is kept."""

    purpose: Note | None = None
    where_to_get: Note | None = None
    required: bool | None = None  # only an FDE entry's: an ISATVON name is always required
    test_mode: ConnectionTestMode | None = None
    test_config: ConnectionTestConfig | None = None

    @field_validator("purpose", "where_to_get")
    @classmethod
    def _no_pasted_value(cls, text: str | None) -> str | None:
        """These notes are exported (`.env.example`): a key pasted into one is refused."""
        if text is not None and SECRET_VALUE.search(text):
            raise ValueError("looks like a secret value")
        return text


# --- responses (none of them can hold a value) ----------------------------------------------------


class Connection(_Strict):
    name: Name
    source: ConnectionSource
    purpose: Note = ""
    where_to_get: Note = ""
    required: bool
    status: ConnectionStatus
    masked_tail: Annotated[str, Field(strict=True, max_length=MAX_TAIL_CHARS)] = ""
    test_mode: ConnectionTestMode = ConnectionTestMode.HTTP
    test_config: ConnectionTestConfig | None = None
    value_set_at: datetime | None = None
    value_set_by: Annotated[str, Field(strict=True, max_length=255)] | None = None
    last_test_at: datetime | None = None
    last_test_outcome: ConnectionTestOutcome | None = None
    row_version: Annotated[int, Field(ge=0)] = 0  # the ETag of the entry (0: no row yet)


class ConnectionList(_Strict):
    project_id: UUID
    connections: list[Connection] = Field(default_factory=list, max_length=MAX_BLOCKERS)


class ConnectionValueResult(_Strict):
    """What a save answers: the entry, which clean-ups ran (names, never the value) and warnings."""

    connection: Connection
    cleaned: list[CleanStep] = Field(default_factory=list, max_length=len(CleanStep))
    unexpected_name: bool = False
    did_you_mean: Name | None = None


class ConnectionTestResult(_Strict):
    name: Name
    outcome: ConnectionTestOutcome
    message: Annotated[str, Field(strict=True, max_length=MAX_TEXT_CHARS)]
    http_status_class: Annotated[int, Field(ge=1, le=5)] | None = None  # 2 for 2xx, and so on
    tested_at: datetime
    connection: Connection


class Blocker(_Strict):
    name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    reason: BlockerReason


class Readiness(_Strict):
    """Whether Commit may go on: every required connection is set and tested (ADR 0030, 10)."""

    workflow_id: UUID
    ready: bool
    blockers: list[Blocker] = Field(default_factory=list, max_length=MAX_BLOCKERS)

    @model_validator(mode="after")
    def _ready_means_no_blockers(self) -> "Readiness":
        if self.ready != (not self.blockers):
            raise ValueError("ready exactly when there is no blocker")
        return self


# The models whose generated schema may never carry a value: a contract test walks them.
RESPONSE_MODELS: Final[tuple[type[BaseModel], ...]] = (
    Connection,
    ConnectionList,
    ConnectionValueResult,
    ConnectionTestResult,
    Readiness,
)


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/connections/`."""
    models: dict[str, type[BaseModel]] = {
        "connection": Connection,
        "connection_list": ConnectionList,
        "value_in": ConnectionValueIn,
        "value_result": ConnectionValueResult,
        "update": ConnectionUpdate,
        "test_result": ConnectionTestResult,
        "readiness": Readiness,
    }
    return {stem: contract_schema(m, f"connections/{stem}") for stem, m in models.items()}
