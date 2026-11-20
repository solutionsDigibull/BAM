"""The byte-deterministic export zip (#133, E14.5). Pure: no storage, no database.

Given (path -> bytes) entries, `write_package` writes a zip that is the same bytes every time:

- entries in sorted path order, then `CHECKSUMS.sha256` last;
- a fixed timestamp (1980-01-01 00:00:00), fixed permissions (0644 regular file, Unix), deflate at
  a fixed level, no directory entries, no symlinks, no extra fields;
- every path is checked: an absolute path, a `..` or empty segment, a backslash, a NUL or another
  control character, a drive letter or a `.env` file other than `.env.example` is refused (zip slip,
  secrets);
- `CHECKSUMS.sha256` is in `sha256sum -c` format (`<hex>  <path>`) and covers every other entry,
  the manifests included.

An entry is either bytes or a zero-argument loader, so the caller can keep one Sub-Skill's files in
memory at a time (500 Sub-Skills stream). The zip is written to a seekable sink (a temp file), never
built in memory.
"""

import hashlib
import re
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import IO, Final

from sbam_contracts.exports import CHECKSUMS_FILE, ENV_EXAMPLE_FILE

FIXED_TIME: Final = (1980, 1, 1, 0, 0, 0)
FILE_MODE: Final = 0o100644  # regular file, rw-r--r--
UNIX: Final = 3
LEVEL: Final = 6
MAX_PATH: Final = 512
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:")

Source = bytes | Callable[[], bytes]


class UnsafePath(ValueError):  # noqa: N818 - the spec's wording
    """A path that could escape the extraction directory, or that names a secret file."""


@dataclass(frozen=True)
class PackageInfo:
    sha256: str  # of the whole zip
    size: int
    entries: tuple[str, ...]  # every entry in zip order, CHECKSUMS.sha256 last


def check_path(path: str) -> str:
    """`path` when it is a safe relative zip path, else `UnsafePath`."""
    if not path or len(path) > MAX_PATH:
        raise UnsafePath("an entry path must be 1-512 characters")
    if "\\" in path or _CONTROL.search(path):
        raise UnsafePath(f"{path[:80]!r}: backslash, NUL or control character")
    if path.startswith("/") or _DRIVE.match(path):
        raise UnsafePath(f"{path[:80]!r}: absolute path")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UnsafePath(f"{path[:80]!r}: empty, '.' or '..' segment")
    name = parts[-1]
    if (name == ".env" or name.startswith(".env.")) and name != ENV_EXAMPLE_FILE:
        raise UnsafePath(f"{path[:80]!r}: a .env file is never exported")
    return path


def checksums(hashes: Mapping[str, str]) -> bytes:
    """`sha256sum -c` lines, sorted by path."""
    return "".join(f"{hashes[path]}  {path}\n" for path in sorted(hashes)).encode()


def _info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = UNIX  # zipfile picks 0 on Windows: fixed so the bytes do not vary
    info.external_attr = FILE_MODE << 16
    return info


def write_package(entries: Mapping[str, Source], sink: IO[bytes]) -> PackageInfo:
    """Write the zip of `entries` (plus `CHECKSUMS.sha256`) to `sink`, which must be seekable."""
    paths = sorted(check_path(path) for path in entries)
    if CHECKSUMS_FILE in paths:
        raise UnsafePath(f"{CHECKSUMS_FILE} is written by the packager")
    if not paths:
        raise ValueError("an export package needs at least one entry")
    hashes: dict[str, str] = {}
    start = sink.tell()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=LEVEL) as zf:
        for path in paths:
            source = entries[path]
            data = source if isinstance(source, bytes) else source()
            hashes[path] = hashlib.sha256(data).hexdigest()
            zf.writestr(_info(path), data, compresslevel=LEVEL)
            del data  # one entry in memory at a time
        zf.writestr(_info(CHECKSUMS_FILE), checksums(hashes), compresslevel=LEVEL)
    end = sink.tell()
    sink.seek(start)
    digest = hashlib.sha256()
    for block in iter(lambda: sink.read(1024 * 1024), b""):
        digest.update(block)
    sink.seek(start)
    return PackageInfo(digest.hexdigest(), end - start, (*paths, CHECKSUMS_FILE))
