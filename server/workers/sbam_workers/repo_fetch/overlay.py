"""The patch overlay: vendor upstream code untouched, change it only by patch files (WG-9, #757).

A repository that the fetcher vendored (WG-7) is stored untouched under `vendor/<repo>@<commit>/`
with its LICENSE and NOTICE. Every change is a separate patch file with a stated reason, kept under
`patches/<repo>@<commit>/NNNN-<slug>.patch`, and the tree after the patches is written to
`overlay/<repo>/`, which is what a Sub-Skill imports and what the AST and static gates read.

Applying a patch is a small unified-diff apply in pure Python. It runs no `patch` binary and no
script, and it is strict so that the result is reproducible byte for byte: a hunk must match the
file exactly where it says (no fuzz, no offset), a path outside the tree or a LICENSE / NOTICE /
COPYING file is refused, and a patch that does not apply is an error, never a guess. A diff cannot
create or delete an empty file (it has no hunk), so such a patch is refused as empty.
"""

import difflib
import hashlib
import io
import json
import re
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from sbam_contracts.repo_overlay import (
    OVERLAY_ROOT,
    PATCH_ROOT,
    VENDOR_ROOT,
    PatchRecord,
    RepoOverlay,
)

from sbam_workers.repo_fetch.tree import safe_path

# LICENSE, LICENCE, NOTICE, COPYING (and LICENSE.md, LICENSE-MIT ...), at any depth.
PROTECTED = re.compile(r"^(licen[cs]e|notice|copying|copyright|unlicense)([.-][\w.-]+)?$", re.I)
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NO_EOL = "\\ No newline at end of file"
MAX_BUNDLE_FILES = 20_000


class OverlayError(Exception):
    """A patch or overlay that cannot be accepted. `code` is stable; `message` is for a person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Patch:
    name: str  # a short label, used for the file name
    body: bytes  # the unified diff
    reason: str  # why the change was made


@dataclass(frozen=True)
class OverlayBuild:
    overlay: RepoOverlay
    layout: dict[str, bytes]  # path in the code bundle -> bytes: vendor, patches and overlay trees


def is_protected(path: str) -> bool:
    return bool(PROTECTED.match(path.rsplit("/", 1)[-1]))


def repo_name(url: str) -> str:
    """The last path segment of the repository URL, without `.git`, safe as a directory name."""
    parts = urlsplit(url)
    last = [s for s in parts.path.split("/") if s][-1:] or [parts.hostname or "repo"]
    name = re.sub(r"[^A-Za-z0-9._-]", "-", last[0].removesuffix(".git")).strip(".-")
    return name or "repo"


def repo_dir(url: str, commit: str) -> str:
    return f"{VENDOR_ROOT}/{repo_name(url)}@{commit}"


def tree_digest(files: Mapping[str, bytes]) -> str:
    """sha256 over the sorted (path, sha256 of the content) pairs: the same tree gives the same
    digest whatever the order or the packaging it came in."""
    pairs = sorted([path, hashlib.sha256(data).hexdigest()] for path, data in files.items())
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode()).hexdigest()


# --- unified diff: make and apply --------------------------------------------------------------


def _text(data: bytes) -> str:
    return data.decode("utf-8", "surrogateescape")  # byte-transparent for any content


def _bytes(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def _split(text: str) -> list[str]:
    """The lines of a text, each with its "\\n" except a last line that has none. Only "\\n" ends a
    line (not "\\r", "\\x0b", or Unicode separators), so a file round-trips exactly."""
    if not text:
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def make_patch(path: str, old: bytes | None, new: bytes | None) -> bytes:
    """A unified diff turning `old` into `new` for one file (`None` = absent). Empty when equal."""
    if old == new:
        return b""
    before = _split(_text(old)) if old is not None else []
    after = _split(_text(new)) if new is not None else []
    diff = difflib.unified_diff(
        before,
        after,
        fromfile="/dev/null" if old is None else f"a/{path}",
        tofile="/dev/null" if new is None else f"b/{path}",
        n=3,
    )
    out: list[str] = []
    for line in diff:
        out.append(line)
        if line[:1] in " -+" and not line.startswith(("---", "+++")) and not line.endswith("\n"):
            out.append("\n" + _NO_EOL + "\n")  # the file's last line has no newline
    return _bytes("".join(out))


@dataclass
class _Hunk:
    old_start: int
    old: list[str]  # lines the hunk expects, with their line ends
    new: list[str]  # lines it leaves


@dataclass
class _FileDiff:
    path: str
    create: bool
    delete: bool
    hunks: list[_Hunk]


def _path_of(raw: str) -> str | None:
    raw = raw.split("\t")[0].strip()
    if raw == "/dev/null":
        return None
    return raw[2:] if raw[:2] in ("a/", "b/") else raw


def _parse(body: bytes) -> list[_FileDiff]:
    if not body.strip():
        raise OverlayError("PATCH_EMPTY", "the patch is empty")
    text = _text(body)
    if not text.endswith("\n"):
        raise OverlayError("PATCH_SYNTAX", "the patch does not end with a newline")
    lines = text.split("\n")[:-1]
    diffs: list[_FileDiff] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("--- "):
            i += 1  # "diff --git", "index" and similar header lines are not needed
            continue
        if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
            raise OverlayError("PATCH_SYNTAX", f"line {i + 1}: '---' is not followed by '+++'")
        old_path, new_path = _path_of(lines[i][4:]), _path_of(lines[i + 1][4:])
        if old_path is not None and new_path is not None and old_path != new_path:
            raise OverlayError("PATCH_SYNTAX", f"a rename is not supported: {old_path}")
        target = new_path if new_path is not None else old_path
        if target is None:
            raise OverlayError("PATCH_SYNTAX", f"line {i + 1}: both sides are /dev/null")
        fd = _FileDiff(target, old_path is None, new_path is None, [])
        i += 2
        while i < len(lines) and (match := _HUNK.match(lines[i])):
            old_len = int(match[2]) if match[2] is not None else 1
            new_len = int(match[4]) if match[4] is not None else 1
            hunk = _Hunk(int(match[1]), [], [])
            i += 1
            while (len(hunk.old) < old_len or len(hunk.new) < new_len) and i < len(lines):
                line = lines[i]
                tag, content = line[:1], line[1:] + "\n"
                if tag == " ":
                    hunk.old.append(content)
                    hunk.new.append(content)
                elif tag == "-":
                    hunk.old.append(content)
                elif tag == "+":
                    hunk.new.append(content)
                else:
                    raise OverlayError("PATCH_SYNTAX", f"line {i + 1}: unexpected {line[:20]!r}")
                i += 1
                while i < len(lines) and lines[i] == _NO_EOL:
                    # the previous line has no newline: on the old side, the new side, or both
                    if tag in " -":
                        hunk.old[-1] = hunk.old[-1][:-1]
                    if tag in " +":
                        hunk.new[-1] = hunk.new[-1][:-1]
                    i += 1
            if len(hunk.old) != old_len or len(hunk.new) != new_len:
                raise OverlayError(
                    "PATCH_SYNTAX", f"a hunk of {fd.path} is shorter than its header"
                )
            fd.hunks.append(hunk)
        if not fd.hunks:
            raise OverlayError("PATCH_EMPTY", f"{fd.path} has no hunk")
        diffs.append(fd)
    if not diffs:
        raise OverlayError("PATCH_EMPTY", "the patch contains no file change")
    return diffs


def _apply_file(path: str, content: bytes | None, fd: _FileDiff) -> bytes | None:
    """The file after `fd`; none when it is deleted. A hunk must match exactly where it says."""
    if fd.create and content is not None:
        raise OverlayError("PATCH_FILE_EXISTS", f"{path} already exists")
    if not fd.create and content is None:
        raise OverlayError("PATCH_MISSING_FILE", f"{path} does not exist")
    lines = _split(_text(content)) if content is not None else []
    result: list[str] = []
    pos = 0
    for number, hunk in enumerate(fd.hunks, 1):
        start = hunk.old_start if not hunk.old else hunk.old_start - 1
        if (hunk.old and hunk.old_start < 1) or start < pos:
            raise OverlayError("PATCH_CONTEXT", f"{path}: hunk {number} is out of order")
        if lines[start : start + len(hunk.old)] != hunk.old:
            raise OverlayError("PATCH_CONTEXT", f"{path}: hunk {number} does not match the file")
        result.extend(lines[pos:start])
        result.extend(hunk.new)
        pos = start + len(hunk.old)
    result.extend(lines[pos:])
    if fd.delete:
        if result:
            raise OverlayError("PATCH_CONTEXT", f"{path}: the deletion leaves lines behind")
        return None
    return _bytes("".join(result))


def apply_patches(
    vendor: Mapping[str, bytes], patches: Sequence[Patch]
) -> tuple[dict[str, bytes], list[list[str]]]:
    """The tree after the patches, applied in order, and the paths each patch touched. The
    vendored mapping is never changed."""
    tree = dict(vendor)
    touched: list[list[str]] = []
    for patch in patches:
        paths: list[str] = []
        for fd in _parse(patch.body):
            if not safe_path(fd.path):
                raise OverlayError("PATCH_PATH", f"{fd.path!r} is not a path inside the tree")
            if is_protected(fd.path):
                raise OverlayError(
                    "PATCH_PROTECTED", f"{fd.path} is a licence file and stays as is"
                )
            after = _apply_file(fd.path, tree.get(fd.path), fd)
            if after is None:
                del tree[fd.path]
            else:
                tree[fd.path] = after
            paths.append(fd.path)
        touched.append(paths)
    return tree, touched


# --- the overlay record and the bundle layout ---------------------------------------------------


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "patch"


def build_overlay(
    *,
    url: str,
    commit: str,
    vendor: Mapping[str, bytes],
    patches: Sequence[Patch] = (),
    licence: str | None = None,
    scan_report_sha256: str | None = None,
) -> OverlayBuild:
    """Apply the patches to the untouched tree, and lay out what goes into the code bundle:
    `vendor/<repo>@<commit>/` (untouched), `patches/<repo>@<commit>/NNNN-<slug>.patch` and
    `overlay/<repo>/` (the tree after the patches)."""
    name = repo_name(url)
    base = repo_dir(url, commit)
    for path in vendor:
        if not safe_path(path):
            raise OverlayError("UNSAFE_VENDOR_PATH", f"{path!r} is not a path inside the tree")
    patched, touched = apply_patches(vendor, patches)
    records: list[PatchRecord] = []
    layout: dict[str, bytes] = {}
    for path, data in vendor.items():
        layout[f"{base}/{path}"] = data
    for index, (patch, files) in enumerate(zip(patches, touched, strict=True), 1):
        where = f"{PATCH_ROOT}/{name}@{commit}/{index:04d}-{_slug(patch.name)}.patch"
        layout[where] = patch.body
        records.append(
            PatchRecord(
                path=where,
                sha256=hashlib.sha256(patch.body).hexdigest(),
                reason=patch.reason,
                files=sorted(set(files)),
            )
        )
    for path, data in patched.items():
        layout[f"{OVERLAY_ROOT}/{name}/{path}"] = data
    overlay = RepoOverlay(
        url=url,
        commit=commit,
        licence=licence,
        scan_report_sha256=scan_report_sha256,
        repo_dir=base,
        licence_files=sorted(path for path in vendor if is_protected(path)),
        vendor_tree_sha256=tree_digest(vendor),
        patches=records,
        patched_tree_sha256=tree_digest(patched),
    )
    return OverlayBuild(overlay, layout)


def _under(layout: Mapping[str, bytes], prefix: str) -> dict[str, bytes]:
    cut = len(prefix) + 1
    return {path[cut:]: data for path, data in layout.items() if path.startswith(prefix + "/")}


def verify_layout(layout: Mapping[str, bytes], overlay: RepoOverlay) -> list[str]:
    """What is wrong with a code bundle's overlay; empty when it is right. It re-applies the patches
    from the bundle to the vendored tree in the bundle and compares the result byte for byte with
    the patched tree in the bundle, and checks that the licence files are the upstream's."""
    problems: list[str] = []
    name = overlay.repo_dir.split("/", 1)[1].split("@", 1)[0]
    vendor = _under(layout, overlay.repo_dir)
    shipped = _under(layout, f"{OVERLAY_ROOT}/{name}")
    if tree_digest(vendor) != overlay.vendor_tree_sha256:
        problems.append("VENDOR_TREE_CHANGED")
    patches: list[Patch] = []
    for record in overlay.patches:
        body = layout.get(record.path)
        if body is None:
            problems.append(f"PATCH_MISSING: {record.path}")
        elif hashlib.sha256(body).hexdigest() != record.sha256:
            problems.append(f"PATCH_CHANGED: {record.path}")
        else:
            patches.append(Patch(record.path, body, record.reason))
    if len(patches) == len(overlay.patches):
        try:
            expected, _ = apply_patches(vendor, patches)
        except OverlayError as exc:
            problems.append(f"PATCH_DOES_NOT_APPLY: {exc.code}")
        else:
            for path in sorted(set(expected) | set(shipped)):
                if expected.get(path) != shipped.get(path):
                    problems.append(f"PATCHED_TREE_DIFFERS: {path}")
    if tree_digest(shipped) != overlay.patched_tree_sha256:
        problems.append("PATCHED_TREE_DIGEST")
    for path in overlay.licence_files:
        if path not in vendor or shipped.get(path) != vendor[path]:
            problems.append(f"LICENCE_FILE_CHANGED: {path}")
    return problems


def read_tree_tar(data: bytes) -> dict[str, bytes]:
    """The files of a vendored bundle written by the fetcher (a plain tar of regular files)."""
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        for member in tar:
            if not member.isreg() or not safe_path(member.name):
                raise OverlayError(
                    "UNSAFE_BUNDLE", f"{member.name!r} is not a regular file in the tree"
                )
            if len(files) >= MAX_BUNDLE_FILES:
                raise OverlayError("UNSAFE_BUNDLE", "the bundle has too many files")
            handle = tar.extractfile(member)
            files[member.name] = handle.read() if handle else b""
    return files
