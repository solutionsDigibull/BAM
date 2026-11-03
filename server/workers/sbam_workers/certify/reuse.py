"""Certification reuse for an unchanged Sub-Skill (E14.3): when may a new Workflow version carry an
earlier certificate instead of rebuilding?

The reuse key is not the manifest hash: that includes the version and timestamps, which change on
every build. It is made of the parts that decide what the Sub-Skill *is*:

  * `isatvon_definition` and `contracts` (canonical JSON hashes),
  * the Sub-Skill's identity and name (an FDE rename is a change),
  * the template's version and sha256 (a patch release with identical output is still another
    template),
  * the runtime image digest (a platform-wide bump means no reuse anywhere),
  * the test suite's hash (a case the FDE added is another suite).

`decide` is pure: it compares two sets of parts and names every part that differs, so the audit row
can carry the compared hashes and a rebuild can say why. The caller adds the one thing that needs
storage, whether the original bundle still hashes to what was certified (`evidence_verified`); when
it does not, reuse is refused.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from sbam_contracts.canonical import sha256_of

PARTS: Final = (
    "skill_id",
    "skill_name",
    "isatvon_definition",
    "contracts",
    "template",
    "runtime_image_digest",
    "suite_sha256",
)
EVIDENCE_TAMPERED: Final = "EVIDENCE_TAMPERED"
NO_PRIOR_CERTIFICATE: Final = "NO_PRIOR_CERTIFICATE"


@dataclass(frozen=True)
class ReuseParts:
    """The hash of each part of the reuse key. Compared part by part, never as one blob, so a
    difference can be named."""

    hashes: Mapping[str, str]

    @property
    def key(self) -> str:
        return sha256_of(dict(self.hashes))


@dataclass(frozen=True)
class ReuseDecision:
    reuse: bool
    differing: tuple[str, ...] = ()
    reason: str = ""
    prior_key: str | None = None
    current_key: str = ""


def parts_of(manifest: Mapping[str, Any], suite_sha256: str) -> ReuseParts:
    """The reuse parts of a Sub-Skill manifest (any status) and its current suite hash."""
    meta = manifest["skill_metadata"]
    build = manifest["sbam_build_record"]["build"]
    template = build["template"]
    return ReuseParts(
        {
            "skill_id": sha256_of(meta["skill_id"]),
            "skill_name": sha256_of(meta["skill_name"]),
            "isatvon_definition": sha256_of(manifest["isatvon_definition"]),
            "contracts": sha256_of(manifest["contracts"]),
            "template": sha256_of(
                {"version": template["version"], "sha256": _digest(template["sha256"])}
            ),
            "runtime_image_digest": sha256_of(_digest(build["runtime_image_digest"])),
            "suite_sha256": sha256_of(_digest(suite_sha256)),
        }
    )


def _digest(value: str) -> str:
    return value.removeprefix("sha256:")


def decide(
    prior: ReuseParts | None, current: ReuseParts, *, evidence_verified: bool
) -> ReuseDecision:
    """Reuse only when every part is identical and the original evidence still verifies."""
    if prior is None:
        return ReuseDecision(False, reason=NO_PRIOR_CERTIFICATE, current_key=current.key)
    differing = tuple(part for part in PARTS if prior.hashes.get(part) != current.hashes.get(part))
    if differing:
        return ReuseDecision(
            False,
            differing,
            "PARTS_CHANGED",
            prior_key=prior.key,
            current_key=current.key,
        )
    if not evidence_verified:
        return ReuseDecision(
            False, (), EVIDENCE_TAMPERED, prior_key=prior.key, current_key=current.key
        )
    return ReuseDecision(True, (), "", prior_key=prior.key, current_key=current.key)
