"""Check every input before a VM exists (E13.2 safety checks, ADR 0021).

Nothing here talks to a backend. A mismatch raises `HashMismatch` (reason HASH_MISMATCH): the run is
INFRA_ERROR, no VM is created and the message names what failed, never its content.

Rules worth knowing
  * The bundle tar is untrusted bytes until proven otherwise: plain USTAR only, regular files only,
    flat names that match the assembler's own pattern, no duplicate member (a later entry would
    silently win), size and count caps, and every byte read must equal the size in the header.
  * The proof is the assembler's: `make_bundle` re-hashes the files, and the per-file hashes and
    `bundle_sha256` must equal what `code_attempts` recorded: one implementation of the rules.
  * Comparisons of hashes are constant time.
"""

import hashlib
import hmac
import io
import tarfile
from collections.abc import Mapping
from typing import Final

from sbam_contracts.sandbox import InfraReason

from sbam_workers.build.codegen.bundle import make_bundle
from sbam_workers.sandbox.backend import SandboxInfraError

MAX_BUNDLE_FILES: Final = 64
MAX_FILE_BYTES: Final = 1024 * 1024
MAX_BUNDLE_BYTES: Final = 8 * 1024 * 1024


class HashMismatch(SandboxInfraError):  # noqa: N818 - the name the E13.2 issue and TD §13.3 use
    def __init__(self, what: str) -> None:
        super().__init__(InfraReason.HASH_MISMATCH, f"{what} does not match its recorded hash")
        self.what = what


def _hex(value: str) -> str:
    return value.removeprefix("sha256:")


def same_hash(actual: str, expected: str) -> bool:
    """Constant-time equality of two sha256 hex strings, with or without the `sha256:` prefix."""
    return hmac.compare_digest(_hex(actual).encode(), _hex(expected).encode())


def verify_sha(data: bytes, expected: str, what: str) -> None:
    if not same_hash(hashlib.sha256(data).hexdigest(), expected):
        raise HashMismatch(what)


def read_bundle_tar(tar_bytes: bytes) -> dict[str, bytes]:
    """The files of a bundle tar, or `HashMismatch("bundle")` for anything but a plain one."""
    if len(tar_bytes) > MAX_BUNDLE_BYTES + 64 * 1024:
        raise HashMismatch("bundle")
    files: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
            members = tar.getmembers()
            if len(members) > MAX_BUNDLE_FILES:
                raise HashMismatch("bundle")
            for member in members:
                # a duplicate would let a later entry replace the one that was hashed
                if member.name in files or not member.isreg():
                    raise HashMismatch("bundle")
                if member.size > MAX_FILE_BYTES:
                    raise HashMismatch("bundle")
                stream = tar.extractfile(member)
                if stream is None:
                    raise HashMismatch("bundle")
                data = stream.read(MAX_FILE_BYTES + 1)
                total += len(data)
                if len(data) != member.size or total > MAX_BUNDLE_BYTES:
                    raise HashMismatch("bundle")
                files[member.name] = data
    except (tarfile.TarError, EOFError, OSError, ValueError) as exc:
        raise HashMismatch("bundle") from exc
    return files


def verify_bundle(
    tar_bytes: bytes, expected_files: Mapping[str, str], expected_bundle_sha256: str
) -> dict[str, bytes]:
    """The verified files: the tar's content hashes to exactly what `code_attempts` recorded."""
    files = read_bundle_tar(tar_bytes)
    try:
        bundle = make_bundle(files)  # also refuses any name the assembler would not have written
    except ValueError as exc:
        raise HashMismatch("bundle") from exc
    recorded = {name: _hex(digest) for name, digest in expected_files.items()}
    if set(bundle.hashes) != set(recorded) or not all(
        same_hash(bundle.hashes[name], recorded[name]) for name in recorded
    ):
        raise HashMismatch("bundle")
    if not same_hash(bundle.bundle_sha256, expected_bundle_sha256):
        raise HashMismatch("bundle")
    return dict(bundle.files)
