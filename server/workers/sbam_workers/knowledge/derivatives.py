"""Where a document version's parsed derivatives are (E4.5 writes them, E5.2/E5.3 read them).

The parse step stores `doc.md` and `doc.json` content-addressed and records their keys on the
version (`derived_md_key`, `derived_json_key`): `<tenant>/<project>/derived/<sha256>`, so a key also
gives the file's sha256. A version with no key, or a key outside its own project's `derived/`
prefix, has no derivative and is NOT_PARSED for a knowledge build.
"""

from dataclasses import dataclass
from typing import Any

from sbam_api.storage.base import Kind

DOC_MD = "doc.md"
DOC_JSON = "doc.json"


@dataclass(frozen=True)
class Derivative:
    storage_key: str
    sha256: str


def derivatives_of(version: Any) -> dict[str, Derivative]:
    """The version's derivatives whose keys are well-formed derived keys of its own project."""
    prefix = f"{version.tenant_id}/{version.project_id}/{Kind.DERIVED.value}/"
    found: dict[str, Derivative] = {}
    for name, key in (
        (DOC_MD, getattr(version, "derived_md_key", None)),
        (DOC_JSON, getattr(version, "derived_json_key", None)),
    ):
        if isinstance(key, str) and key.startswith(prefix):
            sha = key.removeprefix(prefix)
            if len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
                found[name] = Derivative(storage_key=key, sha256=sha)
    return found
