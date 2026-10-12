"""The storage interface (TD §4.2, C-37): every project file goes through a `StorageBackend`.

Objects are content-addressed: the key is `<tenant>/<project>/[<workflow>/]<kind>/<sha256>`, never a
user filename. The same content in the same scope always gets the same key (dedupe), and an object
never changes once written. File metadata (filename, content type, size, sha256) lives in Postgres.
"""

import hashlib
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from typing import IO, BinaryIO, Protocol

from sbam_contracts.api import ErrorCode

from sbam_api.core.conventions import ApiError

CHUNK = 1024 * 1024


class Kind(StrEnum):
    UPLOADS = "uploads"
    DERIVED = "derived"
    OKF = "okf"
    CODE = "code"
    TESTS = "tests"
    EXPORTS = "exports"


@dataclass(frozen=True)
class StorageScope:
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    workflow_id: uuid.UUID | None = None

    def key(self, kind: Kind, sha256: str) -> str:
        parts = [self.tenant_id, self.project_id, self.workflow_id, kind.value, sha256]
        return "/".join(str(p) for p in parts if p is not None)


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    sha256: str
    size: int
    content_type: str


class InvalidStorageKey(ApiError):  # noqa: N818 - name from the E1.6 spec
    def __init__(self) -> None:
        super().__init__(400, ErrorCode.VALIDATION_ERROR, "Invalid storage key")


class StorageUnavailable(ApiError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__(503, ErrorCode.SERVICE_UNAVAILABLE, "Storage is unavailable; retry later")


class StorageIntegrityError(ApiError):
    def __init__(self) -> None:
        super().__init__(500, ErrorCode.HASH_MISMATCH, "Stored object failed its integrity check")


class ObjectTooLarge(ApiError):  # noqa: N818
    def __init__(self) -> None:
        super().__init__(413, ErrorCode.FILE_TOO_LARGE, "File is too large")


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KEY = re.compile(
    rf"(?P<tenant>{_UUID})/(?P<project>{_UUID})/(?:{_UUID}/)?"
    rf"(?:{'|'.join(k.value for k in Kind)})/(?P<sha256>[0-9a-f]{{64}})"
)


def parse_key(key: str) -> tuple[uuid.UUID, str]:
    """(tenant_id, sha256) of a valid key. Anything else (`..`, absolute, backslash) is rejected."""
    match = _KEY.fullmatch(key)
    if not match:
        raise InvalidStorageKey()
    return uuid.UUID(match["tenant"]), match["sha256"]


class StorageBackend(Protocol):
    def put(
        self,
        data: BinaryIO | bytes,
        scope: StorageScope,
        kind: Kind,
        content_type: str,
        max_bytes: int | None = None,
    ) -> StoredObject: ...

    def open(self, key: str) -> BinaryIO: ...

    def get(self, key: str, *, verify: bool = False) -> bytes: ...

    def exists(self, key: str) -> bool: ...


def chunks(data: BinaryIO | bytes) -> Iterable[bytes]:
    return [data] if isinstance(data, bytes) else iter(partial(data.read, CHUNK), b"")


def hash_into(data: BinaryIO | bytes, sink: IO[bytes], max_bytes: int | None) -> tuple[str, int]:
    """Copy `data` into `sink` while hashing it: (sha256 hex, size). Raises ObjectTooLarge."""
    digest, size = hashlib.sha256(), 0
    for block in chunks(data):
        size += len(block)
        if max_bytes is not None and size > max_bytes:
            raise ObjectTooLarge()
        digest.update(block)
        sink.write(block)
    return digest.hexdigest(), size
