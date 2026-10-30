"""Mock APIs for sandbox tests (TD §15.2, E13.5): what `sbam-mock-api` serves instead of real hosts.

A *mock definition* answers one `S.external_data` API reference: a list of stubs, each an explicit
response (or a per-call sequence of them) for a method and path. A stub may be tied to one test case
(`case`), so a PASS_AFTER_RETRY case can see 503 then 200 on a route another case sees 200 on.
Definitions are stored with the dataset (one artifact each) and their hashes go into the dataset's
`sha256`, so a certification run can be reproduced from the evidence.

Rules the models enforce, and the server repeats because it never trusts its caller:
* No forwarding. A stub is a status and a JSON body, nothing else: `proxy_base_url`, `record`,
  `passthrough` and the like are refused by name, and any other unknown key by `extra="forbid"`.
* No credentials in a body (private keys, AWS key ids, JWTs, bearer tokens, vendor API keys).
* Bounded: 1 MiB per body, 10 responses per sequence, 200 stubs per definition, 400 per run.
* `/health` and `/_control` are the service's own and cannot be stubbed.

Wire protocol (the sandbox adapter and the worker are its callers; the service is stdlib only):
  PUT/DELETE /_control/runs/{run_id}   bearer token; `MockRunSetup` in, namespace per run
  GET /_control/runs/{run_id}/journal  bearer token; `MockJournal` out
  anything else                        the tool's own call: `X-Sbam-Run-Id` selects the namespace,
                                       `X-Sbam-Case` (optional) the case; authorization is ignored.
"""

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.canonical import sha256_of

RUN_HEADER: Final = "X-Sbam-Run-Id"
CASE_HEADER: Final = "X-Sbam-Case"
MOCK_UNMATCHED: Final = "MOCK_UNMATCHED"  # the 404 problem code of a request no stub answers
UNMATCHED: Final = "UNMATCHED"  # a journal entry's `stub_id` for it
MAX_BODY_BYTES: Final = 1024 * 1024
MAX_SEQUENCE: Final = 10
MAX_STUBS: Final = 200
MAX_RUN_STUBS: Final = 400
RESERVED_PATHS: Final = ("/health", "/_control")
# Keys that would turn a stub into a forwarder or a recorder. Refused by name so the error says why.
FORBIDDEN_MODES: Final = frozenset(
    {
        "proxy",
        "proxy_base_url",
        "proxy_url",
        "proxy_pass",
        "record",
        "recording",
        "record_mode",
        "passthrough",
        "pass_through",
        "forward",
        "forward_to",
        "upstream",
        "target_url",
        "base_url",
    }
)
# Shapes of real credentials; a test fixture does not need one.
CREDENTIAL_PATTERNS: Final = tuple(
    re.compile(p)
    for p in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",  # a JWT
        r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]{20,}",
        r"\b(?:sk|pk|rk)[-_](?:live|test)?[-_]?[A-Za-z0-9]{20,}",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    )
)

Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
StubId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
CaseName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
DefinitionName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
RunId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")]
# A path or a template: `{name}` stands for exactly one segment. No query, no scheme, no `..`.
Path = Annotated[
    str, StringConstraints(max_length=512, pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/{}-]*$")
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _no_forwarding(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            banned = sorted(str(k) for k in data if str(k).lower() in FORBIDDEN_MODES)
            if banned:
                raise ValueError(
                    f"{', '.join(banned)}: mocks never forward or record; every stub answers itself"
                )
        return data


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (_strings(k) + _strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


class MockResponse(_Strict):
    """One answer: a status and a JSON body (absent = an empty body)."""

    status: Annotated[int, Field(strict=True, ge=100, le=599)]
    body: JsonValue = None

    @field_validator("body")
    @classmethod
    def _body_is_small_and_clean(cls, body: JsonValue) -> JsonValue:
        if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError(f"body is larger than {MAX_BODY_BYTES} bytes")
        if any(p.search(s) for s in _strings(body) for p in CREDENTIAL_PATTERNS):
            raise ValueError("body looks like it holds a real credential; use a made-up value")
        return body


class MockStub(_Strict):
    """What one route answers. `responses` is the per-call sequence: the first call gets the first
    entry, and the last entry answers every call after that."""

    id: StubId
    method: Method
    path: Path
    case: CaseName | None = Field(
        default=None, description="only for this case; absent = every case without its own stub"
    )
    responses: list[MockResponse] = Field(min_length=1, max_length=MAX_SEQUENCE)

    @field_validator("path")
    @classmethod
    def _path_is_plain(cls, path: str) -> str:
        if ".." in path.split("/") or "//" in path:
            raise ValueError("path must not contain '..' or empty segments")
        if any(path == r or path.startswith(r + "/") for r in RESERVED_PATHS):
            raise ValueError("/health and /_control belong to the service")
        return path


class MockDefinition(_Strict):
    """The stubs for one API reference (`S.external_data` entry `name`)."""

    name: DefinitionName
    stubs: list[MockStub] = Field(min_length=1, max_length=MAX_STUBS)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [s.id for s in self.stubs]
        if len(set(ids)) != len(ids):
            raise ValueError("stub ids must be unique")
        routes = [(s.case, s.method, s.path) for s in self.stubs]
        if len(set(routes)) != len(routes):
            raise ValueError("two stubs answer the same method and path for the same case")
        return self


class MockRunSetup(_Strict):
    """`PUT /_control/runs/{run_id}`: every definition of the dataset, for one sandbox run."""

    definitions: list[MockDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def _within_the_run_limits(self) -> Self:
        names = [d.name for d in self.definitions]
        if len(set(names)) != len(names):
            raise ValueError("definition names must be unique")
        stubs = [s for d in self.definitions for s in d.stubs]
        if len(stubs) > MAX_RUN_STUBS:
            raise ValueError(f"at most {MAX_RUN_STUBS} stubs per run")
        ids = [s.id for s in stubs]
        if len(set(ids)) != len(ids):
            raise ValueError("stub ids must be unique across the run")
        routes = [(s.case, s.method, s.path) for s in stubs]
        if len(set(routes)) != len(routes):  # two references must not answer one route
            raise ValueError("two stubs answer the same method and path for the same case")
        return self


class JournalEntry(_Strict):
    """One request the service saw, in order. Bodies are never recorded."""

    n: Annotated[int, Field(ge=1)]
    case: str | None
    method: str
    path: str
    stub_id: str  # the stub's id, or `UNMATCHED`
    response_index: int | None  # which entry of the stub's sequence answered; None if unmatched
    status: int


class MockJournal(_Strict):
    """`GET /_control/runs/{run_id}/journal`: the run's request journal, stored with its logs."""

    run_id: RunId
    entries: list[JournalEntry]
    dropped: int = Field(default=0, description="oldest entries discarded once the journal is full")


def definition_content(definition: MockDefinition) -> dict[str, Any]:
    """The stored form of a definition (JSON, canonical when serialised)."""
    return definition.model_dump(mode="json")


def definition_sha256(definition: MockDefinition) -> str:
    """64 hex over the definition's canonical JSON: the value `dataset_sha256(mocks=...)` takes."""
    return sha256_of(definition_content(definition)).removeprefix("sha256:")


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/mocks/`."""
    models: dict[str, type[BaseModel]] = {
        "mock_definition": MockDefinition,
        "mock_run_setup": MockRunSetup,
        "mock_journal": MockJournal,
    }
    return {stem: contract_schema(model, f"mocks/{stem}") for stem, model in models.items()}
