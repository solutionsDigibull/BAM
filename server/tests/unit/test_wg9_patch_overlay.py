"""WG-9 (#757): a vendored repository stays untouched; every change is a patch file with a reason.

Pure: no git, no network, no database. The overlay library makes and applies the patches itself.
"""

import hashlib
import json

import pytest
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon14 import Library
from sbam_contracts.manifest import Build, TemplateRef
from sbam_contracts.repo_overlay import (
    OVERLAY_ROOT,
    PATCH_ROOT,
    VENDOR_ROOT,
    RepoOverlay,
    overlay_digest,
    repos_reuse_digest,
)
from sbam_contracts.repo_scan import DependencyLicence, LicenceVerdict
from sbam_workers.build.gates.ast_checks import parse_file
from sbam_workers.repo_fetch.overlay import (
    OverlayError,
    Patch,
    apply_patches,
    build_overlay,
    is_protected,
    make_patch,
    read_tree_tar,
    repo_dir,
    repo_name,
    tree_digest,
    verify_layout,
)
from sbam_workers.repo_fetch.report import bundle_tar
from sbam_workers.repo_fetch.sbom import build_sbom, sbom_bytes
from sbam_workers.repo_fetch.static import pattern_scan

URL = "https://github.com/acme/pcb-tools"
COMMIT = "a" * 40
LIB = b'def total(items):\n    return sum(items)\n\n\ndef label(name):\n    return "item-" + name\n'
VENDOR = {
    "LICENSE": b"MIT License\n\nCopyright (c) Acme\n",
    "NOTICE": b"Acme PCB tools\n",
    "docs/LICENSE-MIT": b"MIT again\n",
    "pcb/lib.py": LIB,
    "pcb/crlf.txt": b"one\r\ntwo\r\nthree\r\n",
    "pcb/no_eol.txt": b"first\nlast without newline",
    "pcb/empty.txt": b"",
}


def edit(path: str, new: bytes, reason: str = "fix") -> Patch:
    return Patch(f"fix {path}", make_patch(path, VENDOR[path], new), reason)


def overlay_for(
    *patches: Patch, vendor: dict[str, bytes] | None = None
) -> tuple[RepoOverlay, dict[str, bytes]]:
    built = build_overlay(
        url=URL, commit=COMMIT, vendor=vendor or VENDOR, patches=patches, licence="MIT"
    )
    return built.overlay, built.layout


# --- WG9-T01 reproducible apply -----------------------------------------------------------------


def test_wg9_t01_a_patch_reproduces_the_new_file_byte_for_byte() -> None:
    new = LIB.replace(b"sum(items)", b"float(sum(items))")
    patched, touched = apply_patches(VENDOR, [edit("pcb/lib.py", new)])
    assert patched["pcb/lib.py"] == new
    assert touched == [["pcb/lib.py"]]
    assert {k: v for k, v in patched.items() if k != "pcb/lib.py"} == {
        k: v for k, v in VENDOR.items() if k != "pcb/lib.py"
    }


@pytest.mark.parametrize(
    ("path", "new"),
    [
        ("pcb/crlf.txt", b"one\r\nTWO\r\nthree\r\nfour\r\n"),  # CRLF line ends survive
        ("pcb/no_eol.txt", b"first\nlast without newline, changed"),  # no newline at the end
        ("pcb/no_eol.txt", b"first\nlast without newline\n"),  # a newline is added
        ("pcb/crlf.txt", b"one\r\ntwo\r\nthree"),  # the final newline is removed
        ("pcb/empty.txt", b"now it has text\n"),
        ("pcb/lib.py", b"\xff\xfe not utf-8 \x00 bytes\n"),  # any bytes round-trip
    ],
)
def test_wg9_t01_the_awkward_files_round_trip(path: str, new: bytes) -> None:
    patched, _ = apply_patches(VENDOR, [edit(path, new)])
    assert patched[path] == new


def test_wg9_t01_a_file_can_be_created_and_deleted_by_a_patch() -> None:
    create = Patch("add", make_patch("pcb/extra.py", None, b"X = 1\n"), "a constant")
    delete = Patch("drop", make_patch("pcb/no_eol.txt", VENDOR["pcb/no_eol.txt"], None), "unused")
    patched, touched = apply_patches(VENDOR, [create, delete])
    assert patched["pcb/extra.py"] == b"X = 1\n" and "pcb/no_eol.txt" not in patched
    assert touched == [["pcb/extra.py"], ["pcb/no_eol.txt"]]


def test_wg9_t01_an_empty_file_cannot_be_created_by_a_diff() -> None:
    nothing = make_patch("pcb/new_empty.txt", None, b"")  # a diff of nothing has no hunk
    assert nothing == b""
    with pytest.raises(OverlayError) as err:
        apply_patches(VENDOR, [Patch("empty", nothing, "x")])
    assert err.value.code == "PATCH_EMPTY"


def test_wg9_t01_patches_apply_in_order_and_the_same_way_every_time() -> None:
    first = edit("pcb/lib.py", LIB.replace(b"item-", b"part-"))
    second_new = LIB.replace(b"item-", b"part-").replace(b"sum(items)", b"len(items)")
    second = Patch("b", make_patch("pcb/lib.py", first_applied(first), second_new), "count")
    one, _ = apply_patches(VENDOR, [first, second])
    two, _ = apply_patches(VENDOR, [first, second])
    assert one == two and one["pcb/lib.py"] == second_new


def first_applied(patch: Patch) -> bytes:
    return apply_patches(VENDOR, [patch])[0]["pcb/lib.py"]


def test_wg9_t01_the_layout_verifies_and_rebuilds_the_patched_tree_exactly() -> None:
    patch = edit("pcb/lib.py", LIB.replace(b"item-", b"part-"), "the prefix is part-")
    overlay, layout = overlay_for(patch)
    assert verify_layout(layout, overlay) == []
    base = f"{VENDOR_ROOT}/pcb-tools@{COMMIT}"
    assert overlay.repo_dir == base == repo_dir(URL, COMMIT)
    assert layout[f"{base}/pcb/lib.py"] == LIB  # upstream stays untouched
    assert layout[f"{OVERLAY_ROOT}/pcb-tools/pcb/lib.py"] == LIB.replace(b"item-", b"part-")
    [record] = overlay.patches
    assert record.path == f"{PATCH_ROOT}/pcb-tools@{COMMIT}/0001-fix-pcb-lib-py.patch"
    assert layout[record.path] == patch.body
    assert record.sha256 == hashlib.sha256(patch.body).hexdigest()
    assert record.reason == "the prefix is part-" and record.files == ["pcb/lib.py"]


def test_wg9_t01_a_changed_file_in_the_bundle_is_found() -> None:
    patch = edit("pcb/lib.py", LIB.replace(b"item-", b"part-"))
    overlay, layout = overlay_for(patch)
    tampered = dict(layout)
    tampered[f"{OVERLAY_ROOT}/pcb-tools/pcb/lib.py"] = b"def total(items):\n    return 0\n"
    assert "PATCHED_TREE_DIFFERS: pcb/lib.py" in verify_layout(tampered, overlay)
    upstream = dict(layout)
    upstream[f"{VENDOR_ROOT}/pcb-tools@{COMMIT}/pcb/lib.py"] = b"changed\n"
    assert "VENDOR_TREE_CHANGED" in verify_layout(upstream, overlay)
    swapped = dict(layout)
    swapped[overlay.patches[0].path] = make_patch("pcb/lib.py", LIB, b"other\n")
    assert f"PATCH_CHANGED: {overlay.patches[0].path}" in verify_layout(swapped, overlay)


# --- the apply is strict ------------------------------------------------------------------------


def test_wg9_a_hunk_that_does_not_match_is_an_error_never_a_guess() -> None:
    patch = edit("pcb/lib.py", LIB.replace(b"item-", b"part-"))
    other = dict(VENDOR, **{"pcb/lib.py": LIB.replace(b"def label(name)", b"def label(n)")})
    with pytest.raises(OverlayError) as err:
        apply_patches(other, [patch])
    assert err.value.code == "PATCH_CONTEXT"


def test_wg9_there_is_no_offset_and_no_fuzz() -> None:
    patch = edit("pcb/lib.py", LIB.replace(b"item-", b"part-"))
    shifted = dict(VENDOR, **{"pcb/lib.py": b"# a new first line\n" + LIB})
    with pytest.raises(OverlayError) as err:
        apply_patches(shifted, [patch])  # the same text, one line lower: still refused
    assert err.value.code == "PATCH_CONTEXT"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"", "PATCH_EMPTY"),
        (b"not a patch at all\n", "PATCH_EMPTY"),
        (b"--- a/pcb/lib.py\n+++ b/pcb/lib.py\n@@ -1,1 +1,1 @@\n-x\n+y", "PATCH_SYNTAX"),  # no EOL
        (b"--- a/pcb/lib.py\n+++ b/pcb/other.py\n@@ -1 +1 @@\n-a\n+b\n", "PATCH_SYNTAX"),  # rename
        (b"--- a/pcb/lib.py\n+++ b/pcb/lib.py\n@@ -1,5 +1,5 @@\n-x\n+y\n", "PATCH_SYNTAX"),  # short
        (
            b"--- a/pcb/nothing.py\n+++ b/pcb/nothing.py\n@@ -1 +1 @@\n-a\n+b\n",
            "PATCH_MISSING_FILE",
        ),
        (b"--- /dev/null\n+++ b/pcb/lib.py\n@@ -0,0 +1 @@\n+x\n", "PATCH_FILE_EXISTS"),
    ],
)
def test_wg9_a_bad_patch_is_refused_with_its_reason(body: bytes, code: str) -> None:
    with pytest.raises(OverlayError) as err:
        apply_patches(VENDOR, [Patch("bad", body, "why")])
    assert err.value.code == code


@pytest.mark.parametrize("path", ["../escape.py", "/etc/passwd", "a/../../b.py", ".git/config"])
def test_wg9_a_path_outside_the_tree_is_refused(path: str) -> None:
    body = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+x = 1\n".encode()
    with pytest.raises(OverlayError) as err:
        apply_patches(VENDOR, [Patch("escape", body, "why")])
    assert err.value.code == "PATCH_PATH"


def test_wg9_a_vendored_path_that_is_not_safe_is_refused() -> None:
    with pytest.raises(OverlayError) as err:
        build_overlay(url=URL, commit=COMMIT, vendor={"../x": b"1"})
    assert err.value.code == "UNSAFE_VENDOR_PATH"


# --- WG9-T02 LICENSE and NOTICE preserved -------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["LICENSE", "LICENCE", "license.md", "LICENSE-MIT", "NOTICE", "NOTICE.txt", "COPYING"]
)
def test_wg9_t02_the_licence_file_names_are_protected(name: str) -> None:
    assert is_protected(name) and is_protected(f"docs/deep/{name}")


@pytest.mark.parametrize("name", ["licenses.py", "notice_board.py", "README.md", "lib.py"])
def test_wg9_t02_other_files_are_not(name: str) -> None:
    assert not is_protected(name)


@pytest.mark.parametrize("path", ["LICENSE", "NOTICE", "docs/LICENSE-MIT"])
def test_wg9_t02_a_patch_cannot_touch_a_licence_file(path: str) -> None:
    with pytest.raises(OverlayError) as err:
        apply_patches(VENDOR, [edit(path, b"changed\n")])
    assert err.value.code == "PATCH_PROTECTED"
    with pytest.raises(OverlayError):
        apply_patches(VENDOR, [Patch("drop", make_patch(path, VENDOR[path], None), "no")])


def test_wg9_t02_the_overlay_lists_and_keeps_the_licence_files() -> None:
    overlay, layout = overlay_for(edit("pcb/lib.py", LIB + b"# more\n"))
    assert overlay.licence_files == ["LICENSE", "NOTICE", "docs/LICENSE-MIT"]
    for path in overlay.licence_files:
        assert layout[f"{overlay.repo_dir}/{path}"] == VENDOR[path]
        assert layout[f"{OVERLAY_ROOT}/pcb-tools/{path}"] == VENDOR[path]
    assert verify_layout(layout, overlay) == []


def test_wg9_t02_a_licence_file_changed_in_the_patched_tree_is_found() -> None:
    overlay, layout = overlay_for(edit("pcb/lib.py", LIB + b"# more\n"))
    layout[f"{OVERLAY_ROOT}/pcb-tools/NOTICE"] = b"Acme (edited)\n"
    problems = verify_layout(layout, overlay)
    assert "LICENCE_FILE_CHANGED: NOTICE" in problems


# --- WG9-T03 a changed patch invalidates reuse --------------------------------------------------


def test_wg9_t03_a_changed_patch_changes_the_reuse_key() -> None:
    a, _ = overlay_for(edit("pcb/lib.py", LIB.replace(b"item-", b"part-")))
    b, _ = overlay_for(edit("pcb/lib.py", LIB.replace(b"item-", b"piece-")))
    same, _ = overlay_for(edit("pcb/lib.py", LIB.replace(b"item-", b"part-")))
    assert overlay_digest(a) == overlay_digest(same)
    assert overlay_digest(a) != overlay_digest(b)
    assert repos_reuse_digest([a]) != repos_reuse_digest([b])


def test_wg9_t03_an_added_patch_or_a_new_commit_or_tree_changes_it_too() -> None:
    one, _ = overlay_for(edit("pcb/lib.py", LIB.replace(b"item-", b"part-")))
    two, _ = overlay_for(
        edit("pcb/lib.py", LIB.replace(b"item-", b"part-")),
        edit("pcb/no_eol.txt", b"first\nlast\n"),
    )
    newer = build_overlay(url=URL, commit="b" * 40, vendor=VENDOR).overlay
    older = build_overlay(url=URL, commit=COMMIT, vendor=VENDOR).overlay
    other_tree = build_overlay(url=URL, commit=COMMIT, vendor=dict(VENDOR, extra=b"1")).overlay
    digests = {overlay_digest(o) for o in (one, two, newer, older, other_tree)}
    assert len(digests) == 5


def test_wg9_t03_the_reason_is_documentation_not_part_of_the_key() -> None:
    a, _ = overlay_for(edit("pcb/lib.py", LIB + b"# x\n", reason="one reason"))
    b, _ = overlay_for(edit("pcb/lib.py", LIB + b"# x\n", reason="a better worded reason"))
    assert a.patches[0].reason != b.patches[0].reason
    assert overlay_digest(a) == overlay_digest(b)


def test_wg9_t03_the_order_of_the_repositories_does_not_matter_and_none_is_none() -> None:
    a = build_overlay(url=URL, commit=COMMIT, vendor=VENDOR).overlay
    b = build_overlay(url="https://github.com/acme/other", commit="c" * 40, vendor=VENDOR).overlay
    assert repos_reuse_digest([a, b]) == repos_reuse_digest([b, a])
    assert repos_reuse_digest([]) is None and repos_reuse_digest(None) is None


def build_record(repos: list[RepoOverlay] | None) -> Build:
    return Build(
        template=TemplateRef(name="skill", version="1.0.0", sha256="sha256:" + "0" * 64),
        runtime_image_digest="sha256:" + "1" * 64,
        libraries=[Library(package="requests")],
        schema_sql_sha256="sha256:" + "2" * 64,
        **({"repos": repos} if repos is not None else {}),
    )


def test_wg9_t03_the_manifest_records_the_repo_commit_patches_and_reasons() -> None:
    overlay, _ = overlay_for(edit("pcb/lib.py", LIB + b"# x\n", reason="why it was patched"))
    dumped = build_record([overlay]).dump()
    [repo] = dumped["repos"]
    assert repo["url"] == URL and repo["commit"] == COMMIT and repo["licence"] == "MIT"
    assert repo["patches"][0]["sha256"] == overlay.patches[0].sha256
    assert repo["patches"][0]["reason"] == "why it was patched"


def test_wg9_t03_a_manifest_without_repos_hashes_as_before() -> None:
    assert "repos" not in build_record(None).dump()
    overlay, _ = overlay_for(edit("pcb/lib.py", LIB + b"# x\n"))
    other, _ = overlay_for(edit("pcb/lib.py", LIB + b"# y\n"))
    hashes = {sha256_of(build_record(r).dump()) for r in (None, [overlay], [other])}
    assert len(hashes) == 3  # a repository, and a changed patch, change the manifest hash


# --- the same gates see the patched tree --------------------------------------------------------


def test_wg9_the_patched_tree_passes_the_syntax_gate_and_the_pattern_scan() -> None:
    _, layout = overlay_for(edit("pcb/lib.py", LIB.replace(b"item-", b"part-")))
    shipped = {p: d for p, d in layout.items() if p.startswith(f"{OVERLAY_ROOT}/")}
    for path, data in shipped.items():
        if path.endswith(".py"):
            tree, finding = parse_file(path, data)
            assert finding is None and tree is not None
    assert pattern_scan(shipped) == []


def test_wg9_a_patch_that_breaks_the_code_fails_the_syntax_gate() -> None:
    _, layout = overlay_for(edit("pcb/lib.py", b"def total(items:\n    return sum(items)\n"))
    _, finding = parse_file("pcb/lib.py", layout[f"{OVERLAY_ROOT}/pcb-tools/pcb/lib.py"])
    assert finding is not None and finding.code == "AST_SYNTAX"


def test_wg9_a_patch_that_adds_risky_code_is_seen_by_the_pattern_scan() -> None:
    risky = LIB + b"\nexec(base64.b64decode(PAYLOAD))\n"
    _, layout = overlay_for(edit("pcb/lib.py", risky))
    shipped = {p: d for p, d in layout.items() if p.startswith(f"{OVERLAY_ROOT}/")}
    assert [f.rule for f in pattern_scan(shipped)] == ["EXEC_DECODED"]


# --- WG9-T04 the SBOM ---------------------------------------------------------------------------


def test_wg9_t04_the_sbom_lists_every_component_and_its_version() -> None:
    overlay, _ = overlay_for(edit("pcb/lib.py", LIB + b"# x\n"))
    deps = [
        DependencyLicence(
            ecosystem="PyPI",
            name="numpy",
            version="1.26.4",
            spdx="BSD-3-Clause",
            verdict=LicenceVerdict.ALLOWED,
        ),
        DependencyLicence(
            ecosystem="npm",
            name="@acme/ui",
            version="2.0.1",
            spdx="MIT",
            verdict=LicenceVerdict.ALLOWED,
        ),
    ]
    sbom = build_sbom(
        subject="MS01.1_step_1",
        version="1.0.3",
        overlays=[overlay],
        dependencies=deps,
        libraries=[Library(package="requests", version_constraint=">=2.31")],
    )
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
    assert sbom["metadata"]["component"] == {
        "type": "application",
        "name": "MS01.1_step_1",
        "version": "1.0.3",
    }
    by_name = {c["name"]: c for c in sbom["components"]}
    assert set(by_name) == {"pcb-tools", "numpy", "@acme/ui", "requests"}
    repo = by_name["pcb-tools"]
    assert repo["version"] == COMMIT and repo["licenses"] == [{"license": {"id": "MIT"}}]
    assert repo["hashes"] == [{"alg": "SHA-256", "content": overlay.vendor_tree_sha256}]
    assert {
        "name": "sbam:patch",
        "value": f"{overlay.patches[0].sha256} {overlay.patches[0].path}",
    } in repo["properties"]
    assert by_name["numpy"]["purl"] == "pkg:pypi/numpy@1.26.4"
    assert by_name["@acme/ui"]["purl"] == "pkg:npm/%40acme/ui@2.0.1"
    assert by_name["requests"]["version"] == ">=2.31"
    assert all(c["version"] for c in sbom["components"])


def test_wg9_t04_the_sbom_is_deterministic_and_lists_a_package_once() -> None:
    overlay, _ = overlay_for()
    dep = DependencyLicence(
        ecosystem="PyPI", name="numpy", version="1.26.4", spdx=None, verdict=LicenceVerdict.UNKNOWN
    )
    one = build_sbom(subject="s", version="1", overlays=[overlay], dependencies=[dep, dep])
    two = build_sbom(subject="s", version="1", overlays=[overlay], dependencies=[dep])
    assert sbom_bytes(one) == sbom_bytes(two)
    assert [c["name"] for c in json.loads(sbom_bytes(one))["components"]].count("numpy") == 1
    assert next(c for c in one["components"] if c["name"] == "numpy")["licenses"] == []


# --- helpers ------------------------------------------------------------------------------------


def test_wg9_repo_name_and_the_vendored_bundle_from_the_fetcher() -> None:
    assert repo_name("https://gitlab.com/group/sub/tool.git") == "tool"
    assert repo_name("https://github.com/acme/") == "acme"
    assert repo_dir("https://github.com/acme/pcb-tools", COMMIT) == f"vendor/pcb-tools@{COMMIT}"
    assert read_tree_tar(bundle_tar(VENDOR)) == VENDOR  # the WG-7 bundle is the input
    assert tree_digest(VENDOR) == tree_digest(dict(reversed(VENDOR.items())))
