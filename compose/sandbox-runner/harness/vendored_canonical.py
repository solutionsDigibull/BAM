"""Stands in for `sbam_contracts/canonical.py` inside the VM.

The real module canonicalises with `rfc8785`, which the runtime image does not (and must not) contain:
its package list is the allowed-library list and nothing else. The VM only *reads* a loop spec; it
never hashes or re-serialises one, so the two functions the loop-spec module imports refuse to run.
Hashing stays on the platform, where the bytes the VM was given were already hashed and checked.
"""

from typing import Any


def canonical_json(obj: Any) -> bytes:
    raise RuntimeError("canonical JSON is produced on the platform, not in the sandbox")


def sha256_of(obj: Any) -> str:
    raise RuntimeError("hashing is done on the platform, not in the sandbox")
