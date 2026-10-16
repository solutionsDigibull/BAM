"""Where a document version's parsed derivatives are (E4.5 writes them, E5.2/E5.3 read them).

TD §7: `document_versions.derivative_keys` maps a derivative name to its storage key:
`{"doc.md": "<tenant>/<project>/derived/<sha256>", "doc.json": "..."}`. Derivatives are stored
content-addressed, so the key also gives the file's sha256. Until the loaders (#71) add the
column, no version has derivatives and every document is NOT_PARSED for a knowledge build.
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
    """The version's derivatives that are well-formed derived keys; anything else is ignored."""
    keys = getattr(version, "derivative_keys", None)
    if not isinstance(keys, dict):
        return {}
    prefix = f"{version.tenant_id}/{version.project_id}/{Kind.DERIVED.value}/"
    found: dict[str, Derivative] = {}
    for name, key in keys.items():
        if isinstance(key, str) and key.startswith(prefix):
            sha = key.removeprefix(prefix)
            if len(sha) == 64 and all(c in "0123456789abcdef" for c in sha):
                found[str(name)] = Derivative(storage_key=key, sha256=sha)
    return found
