"""WG-15 (#763): where a file of the Workflow's code comes from, by its place in the bundle."""

from typing import Any

import pytest
from sbam_api.builds.workflow_code import file_tag, overlays_of
from sbam_contracts.code_view import FileTag
from sbam_contracts.repo_overlay import PatchRecord, RepoOverlay

COMMIT = "a" * 40
REPO = f"lib@{COMMIT}"


def overlay(*patched: str) -> RepoOverlay:
    return RepoOverlay(
        url="https://github.com/acme/lib",
        commit=COMMIT,
        licence="MIT",
        repo_dir=f"vendor/{REPO}",
        licence_files=["LICENSE"],
        vendor_tree_sha256="0" * 64,
        patches=(
            [
                PatchRecord(
                    path=f"patches/{REPO}/0001-fix.patch",
                    sha256="1" * 64,
                    reason="a stated reason",
                    files=list(patched),
                )
            ]
            if patched
            else []
        ),
        patched_tree_sha256="2" * 64,
    )


def manifest_of(*repos: RepoOverlay) -> dict[str, Any]:
    return {"build": {"repos": [r.model_dump(mode="json") for r in repos]}}


@pytest.mark.parametrize(
    ("path", "tag"),
    [
        ("action_tool.py", FileTag.GENERATED),
        ("models.py", FileTag.GENERATED),
        (f"vendor/{REPO}/LICENSE", FileTag.REUSED),
        (f"vendor/{REPO}/lib.py", FileTag.REUSED),
        (f"patches/{REPO}/0001-fix.patch", FileTag.CUSTOMIZED),
        ("overlay/lib/lib.py", FileTag.CUSTOMIZED),  # a patch touches it
        ("overlay/lib/other.py", FileTag.REUSED),  # in the overlay tree, but no patch touches it
        ("overlay/lib/pkg/deep.py", FileTag.REUSED),
        ("overlay/unknown/lib.py", FileTag.REUSED),  # a repo the manifest does not record
    ],
)
def test_wg15_t01_the_tag_follows_the_bundle_layout_and_the_recorded_patches(
    path: str, tag: FileTag
) -> None:
    assert file_tag(path, [overlay("lib.py")]) == tag


def test_wg15_t01_without_repositories_every_file_is_generated() -> None:
    assert file_tag("action_tool.py", []) == FileTag.GENERATED
    assert file_tag("overlay/lib/lib.py", []) == FileTag.REUSED  # layout still says untouched


def test_wg15_a_patch_of_one_repository_does_not_customize_another() -> None:
    other = overlay("lib.py").model_copy(update={"repo_dir": f"vendor/other@{COMMIT}"})
    assert file_tag("overlay/lib/lib.py", [other]) == FileTag.REUSED


def test_wg15_overlays_are_read_from_the_manifest_and_a_bad_entry_is_skipped() -> None:
    good = overlay("lib.py")
    manifest = manifest_of(good)
    manifest["build"]["repos"].append({"url": "not a repo"})
    assert overlays_of(manifest) == [good]
    assert overlays_of(None) == [] and overlays_of({}) == [] and overlays_of({"build": {}}) == []
