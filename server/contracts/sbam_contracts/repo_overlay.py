"""The patch overlay of a vendored repository (ADR 0028, decision 2; issue WG-9, #757).

Upstream code is kept untouched under `vendor/<repo>@<commit>/` with its LICENSE and NOTICE. Every
change to it is a separate patch file with a stated reason, and the patched tree is what the
Sub-Skill imports. This is the record of that: the repository, the commit it was pinned to, its
licence, the digest of the untouched tree, each patch (path, sha256, reason, files touched) and the
digest of the patched tree.

Reuse (ADR 0013, C-33). An unchanged Sub-Skill reuses its earlier certification. The repository
commit and the patch hashes are part of the key, so any change to a repository or to a patch is a
change and gives a new PATCH version. `overlay_digest` is one repository's share of that key;
`repos_reuse_digest` is the share of all of a Sub-Skill's repositories, ready to join the other
parts of the key (the isatvon definition, the contracts, the template, the runtime image and the
test suite). The reason of a patch is documentation: it is in the manifest but not in the key.
"""

from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field

from sbam_contracts._jsonschema import contract_schema
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon14 import GitCommit, RepoUrl, ShortText

# Where the pieces of an overlay live inside a code bundle.
VENDOR_ROOT: Final = "vendor"  # vendor/<repo>@<commit>/...  the untouched upstream tree
PATCH_ROOT: Final = "patches"  # patches/<repo>@<commit>/NNNN-<slug>.patch
OVERLAY_ROOT: Final = "overlay"  # overlay/<repo>/...           the tree after the patches

Sha256Hex = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
Reason = Annotated[str, Field(strict=True, min_length=1, max_length=500)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PatchRecord(_Strict):
    path: ShortText  # patches/<repo>@<commit>/0001-<slug>.patch, inside the code bundle
    sha256: Sha256Hex  # of the patch file's bytes
    reason: Reason  # why the change was made; in the manifest, not in the reuse key
    files: list[ShortText] = Field(min_length=1, max_length=100)  # the repository paths it touches


class RepoOverlay(_Strict):
    url: RepoUrl
    commit: GitCommit  # the pinned hash; never a branch name
    licence: ShortText | None = None  # SPDX id from the scan report
    scan_report_sha256: Sha256Hex | None = None  # the WG-7 evidence this tree came from
    repo_dir: ShortText  # vendor/<repo>@<commit>
    licence_files: list[ShortText] = Field(
        default_factory=list, max_length=20
    )  # LICENSE, NOTICE...
    vendor_tree_sha256: Sha256Hex  # digest of the untouched tree (see `tree_digest`)
    patches: list[PatchRecord] = Field(default_factory=list, max_length=50)  # applied in this order
    patched_tree_sha256: Sha256Hex  # digest of the tree after the patches


def overlay_digest(overlay: RepoOverlay) -> str:
    """One repository's share of the certification reuse key: where it came from, which commit and
    tree, and the hash of every patch in order. A different commit, tree or patch gives a different
    digest."""
    return sha256_of(
        {
            "url": overlay.url,
            "commit": overlay.commit,
            "vendor_tree_sha256": overlay.vendor_tree_sha256,
            "patches": [patch.sha256 for patch in overlay.patches],
        }
    )


def repos_reuse_digest(overlays: list[RepoOverlay] | None) -> str | None:
    """The share of all of a Sub-Skill's repositories in the reuse key; none when it uses none.

    The order of the list does not matter (the digests are sorted), so listing the same
    repositories in another order does not force a recertification."""
    if not overlays:
        return None
    return sha256_of(sorted(overlay_digest(overlay) for overlay in overlays))


def schemas() -> dict[str, dict[str, Any]]:
    """File stem -> JSON Schema, for the committed files under `schemas/repo/`."""
    return {"overlay": contract_schema(RepoOverlay, "repo/overlay")}
