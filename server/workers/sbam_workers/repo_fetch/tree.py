"""Copy the files of a pinned commit out as data (WG-7 safety checks).

The tree is read object by object. A symlink (mode 120000), a gitlink (160000, a submodule) or any
other special entry makes the whole tree unsafe, and so does a path that could leave the tree: `..`,
an absolute path, a backslash, a control character, a `.git` part. Nothing is written to disk
here and
nothing runs. Caps stop a huge repository from filling memory.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from sbam_workers.repo_fetch.clone import Git, GitError

MAX_FILES = 5_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
_ENTRY = re.compile(rb"^(\d{6}) (\w+) ([0-9a-f]{40,64})\t(.*)$", re.S)
_BAD_PART = re.compile(r"[\x00-\x1f\x7f\\]")


@dataclass
class Tree:
    files: dict[str, bytes] = field(default_factory=dict)
    unsafe: list[str] = field(default_factory=list)
    too_large: bool = False


def safe_path(path: str) -> bool:
    if not path or path.startswith("/") or len(path) > 1024 or _BAD_PART.search(path):
        return False
    return all(
        p not in ("", ".", "..") and p.lower() != ".git" and len(p) <= 255 for p in path.split("/")
    )


def read_tree(git: Git, repo: Path, commit: str) -> Tree:
    out = git.run("ls-tree", "-r", "-z", "--full-tree", commit, cwd=repo)
    tree = Tree()
    total = 0
    entries = [e for e in out.split(b"\0") if e]
    if len(entries) > MAX_FILES:
        tree.too_large = True
        return tree
    for raw in entries:
        match = _ENTRY.match(raw)
        if not match:
            tree.unsafe.append("?")
            continue
        mode, kind, sha, path_bytes = match.groups()
        try:
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError:
            tree.unsafe.append("?")
            continue
        if mode not in (b"100644", b"100755") or kind != b"blob" or not safe_path(path):
            tree.unsafe.append(path[:200])
            continue
        size = int(git.run("cat-file", "-s", sha.decode(), cwd=repo))
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            tree.too_large = True
            return tree
        try:
            tree.files[path] = git.run("cat-file", "blob", sha.decode(), cwd=repo)
        except GitError:
            tree.unsafe.append(path[:200])
    return tree
