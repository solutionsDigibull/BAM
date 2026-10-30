"""Local disk backend: `STORAGE_ROOT/<tenant>/<project>/[<workflow>/]<kind>/<sha256>`.

Writes are atomic: stream into `.tmp/`, fsync, then rename into place, so a reader sees a whole
object or nothing. Objects are 0444 and never overwritten (same content = same key).
"""

import contextlib
import hashlib
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from sbam_api.core.ids import uuid7
from sbam_api.storage.base import (
    InvalidStorageKey,
    Kind,
    ListedObject,
    StorageIntegrityError,
    StorageScope,
    StorageUnavailable,
    StoredObject,
    chunks,
    hash_into,
    parse_key,
)

STALE_TMP_SECONDS = 3600  # older tmp files are left over from a crash; newer may be in flight


class LocalDiskBackend:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.tmp = self.root / ".tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - STALE_TMP_SECONDS
        for leftover in self.tmp.iterdir():
            with contextlib.suppress(OSError):
                if leftover.stat().st_mtime < cutoff:
                    leftover.unlink()

    def _path(self, key: str) -> Path:
        parse_key(key)
        # normpath, not resolve(): pure string work, so it can't race a concurrent mkdir.
        path = Path(os.path.normpath(self.root / key))
        if not path.is_relative_to(self.root):  # belt and braces: the key regex already forbids it
            raise InvalidStorageKey()
        return path

    def put(
        self,
        data: BinaryIO | bytes,
        scope: StorageScope,
        kind: Kind,
        content_type: str,
        max_bytes: int | None = None,
    ) -> StoredObject:
        tmp = self.tmp / str(uuid7())
        try:
            with tmp.open("wb") as sink:
                sha256, size = hash_into(data, sink, max_bytes)
                sink.flush()
                os.fsync(sink.fileno())
            key = scope.key(kind, sha256)
            final = self._path(key)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():  # dedupe: identical content is already stored
                tmp.unlink()
                with contextlib.suppress(OSError):
                    os.utime(
                        final
                    )  # in use again: the sweep of unreferenced files must not take it
            else:
                try:
                    os.replace(tmp, final)  # atomic
                except OSError:
                    # Another writer stored the same bytes first (Windows refuses to replace its
                    # read-only file). Same content, same key: that's a dedupe, not a failure.
                    if not final.is_file():
                        raise
                    tmp.unlink()
                else:
                    final.chmod(0o444)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise StorageUnavailable() from None
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return StoredObject(key, sha256, size, content_type)

    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        try:
            return path.open("rb")
        except FileNotFoundError:
            raise
        except OSError:
            raise StorageUnavailable() from None

    def get(self, key: str, *, verify: bool = False) -> bytes:
        with self.open(key) as f:
            data = b"".join(chunks(f))
        if verify and hashlib.sha256(data).hexdigest() != parse_key(key)[1]:
            raise StorageIntegrityError()
        return data

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.chmod(0o600)  # objects are 0444, and Windows refuses to delete a read-only file
            path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            raise StorageUnavailable() from None

    def list_keys(self, prefix: str) -> Iterator[ListedObject]:
        """Objects under `<tenant>/<project>/<kind>/` (a directory on disk), lazily. A stray file
        that is not a valid key is skipped, never returned."""
        directory = Path(os.path.normpath(self.root / prefix))
        if not directory.is_relative_to(self.root) or not directory.is_dir():
            return
        for entry in sorted(directory.iterdir()):
            key = f"{prefix.rstrip('/')}/{entry.name}"
            try:
                parse_key(key)
                info = entry.stat()
            except (InvalidStorageKey, OSError):
                continue
            if entry.is_file():
                yield ListedObject(key, info.st_size, datetime.fromtimestamp(info.st_mtime, UTC))
