"""RFC 8785 canonical JSON and SHA-256 hashing for manifests (TD §11.5, issue E0.3).

Rules worth knowing
  * Hashes are computed only over RFC 8785 (JCS) canonical bytes, never over pretty-printed or
    database-reordered JSON. Canonicalisation is the `rfc8785` library (Trail of Bits).
  * NaN, Infinity, integers outside the I-JSON safe range, lone surrogates (non-UTF-8 strings) and
    non-string object keys raise ValueError, so no hash is produced for them.
  * `deterministic_hash` excludes `governance.deterministic_hash`, `sbam_build_record.evidence` and
    `sbam_build_record.certification`; the Workflow hash excludes `integrity` and `certification`.
"""

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

import rfc8785

HASH_PREFIX: Final = "sha256:"
HASH_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
SKILL_HASH_EXCLUDES: Final = (
    ("governance", "deterministic_hash"),
    ("sbam_build_record", "evidence"),
    ("sbam_build_record", "certification"),
)
WORKFLOW_HASH_EXCLUDES: Final = (("integrity",), ("certification",))


def canonical_json(obj: Any) -> bytes:
    """RFC 8785 canonical UTF-8 bytes. Raises ValueError for anything JCS cannot represent."""
    try:
        return rfc8785.dumps(obj)
    except rfc8785.CanonicalizationError as exc:
        raise ValueError(str(exc)) from exc


def sha256_of(obj: Any) -> str:
    """'sha256:' + 64 lowercase hex over the canonical bytes of `obj`."""
    return HASH_PREFIX + hashlib.sha256(canonical_json(obj)).hexdigest()


def _without(document: Mapping[str, Any], excludes: Sequence[tuple[str, ...]]) -> dict[str, Any]:
    pruned = copy.deepcopy(dict(document))
    for path in excludes:
        node: Any = pruned
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(path[-1], None)
    return pruned


def deterministic_hash(manifest: Mapping[str, Any]) -> str:
    """`governance.deterministic_hash` of a Skill Manifest given as a JSON object."""
    return sha256_of(_without(manifest, SKILL_HASH_EXCLUDES))


def workflow_content_hash(workflow_manifest: Mapping[str, Any]) -> str:
    """`integrity.content_sha256` of a Workflow manifest. It covers every skill's deterministic,
    manifest and bundle hashes and the schema SQL hash, because they are all inside the hashed body.
    """
    return sha256_of(_without(workflow_manifest, WORKFLOW_HASH_EXCLUDES))
