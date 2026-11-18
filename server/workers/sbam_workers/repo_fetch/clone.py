"""A pinned, scriptless clone (WG-7, SBAM-T01 safety: no clone-time hooks, no submodule scripts).

The fetcher never checks a tree out. It resolves the branch or tag to a commit hash once, fetches
that
exact commit, and reads the tree objects with `git ls-tree` and `git cat-file`: no working tree,
so no
checkout hooks, no smudge filters, no submodule update and no symlink is ever created. Every git
call
is hardened: no prompt, no system or user config, hooks off, https only, no redirects, no submodule
recursion.
"""

import re
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

GIT_TIMEOUT = 180.0
_HEX = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,119}$")


class GitError(Exception):
    """A git command failed (network, a missing ref, a refused protocol). The message has no
    secrets."""


class Git:
    """One hardened `git` runner. `extra_config` is for tests only (a local mirror, `file`
    protocol)."""

    def __init__(
        self,
        home: Path,
        extra_config: Sequence[tuple[str, str]] = (),
        timeout: float = GIT_TIMEOUT,
    ) -> None:
        self.home, self.extra, self.timeout = home, tuple(extra_config), timeout

    def run(self, *args: str, cwd: Path | None = None, binary: bool = False) -> bytes:
        config = [
            ("core.hooksPath", "/dev/null"),
            ("protocol.allow", "never"),
            ("protocol.https.allow", "always"),
            ("http.followRedirects", "false"),
            ("submodule.recurse", "false"),
            ("fetch.recurseSubmodules", "false"),
            ("transfer.fsckObjects", "true"),
            ("core.fsmonitor", "false"),
            ("core.symlinks", "false"),
            *self.extra,
        ]
        argv = ["git"]
        for key, value in config:
            argv += ["-c", f"{key}={value}"]
        argv += list(args)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_ASKPASS": "/bin/true",
            "LC_ALL": "C",
        }
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, never a shell
                argv, cwd=cwd, env=env, capture_output=True, timeout=self.timeout, check=False
            )
        except subprocess.TimeoutExpired:
            raise GitError(f"git {args[0]} timed out") from None
        except OSError as exc:
            raise GitError(f"git could not start: {type(exc).__name__}") from None
        if done.returncode != 0:
            tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            raise GitError(f"git {args[0]} failed: {tail[0][:200]}")
        return done.stdout


def resolve_commit(git: Git, url: str, ref: str | None) -> str:
    """The commit `ref` (a branch, a tag, a hash, or the default branch) points to, now, once."""
    if ref and _HEX.match(ref):
        return ref
    if ref and not _REF.match(ref):
        raise GitError("the ref is not a plain branch or tag name")
    patterns = (
        [f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/heads/{ref}"] if ref else ["HEAD"]
    )
    out = git.run("ls-remote", "--", url, *patterns).decode("utf-8", "replace")
    found: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if _HEX.match(sha):
            found[name] = sha
    for pattern in patterns:  # a peeled tag first, so an annotated tag gives its commit
        if pattern in found:
            return found[pattern]
    raise GitError(f"the ref {ref or 'HEAD'} was not found")


@contextmanager
def fetched(
    extra_config: Sequence[tuple[str, str]], url: str, ref: str | None
) -> Iterator[tuple[Git, Path, str]]:
    """A scratch repository holding exactly one commit of `url`, and that commit's hash.

    `ref` (a branch, a tag, a hash, or none for the default branch) is resolved once; the commit
    that
    comes back is the one fetched and the one recorded. The scratch directory is removed afterwards.
    """
    with tempfile.TemporaryDirectory(prefix="sbam-fetch-") as tmp:
        root = Path(tmp)
        home = root / "home"
        home.mkdir()
        repo = root / "repo"
        repo.mkdir()
        git = Git(home, extra_config)
        commit = resolve_commit(git, url, ref)
        git.run("init", "-q", "--bare", str(repo))
        git.run(
            "fetch",
            "-q",
            "--depth=1",
            "--no-tags",
            "--no-recurse-submodules",
            "--",
            url,
            commit,
            cwd=repo,
        )
        head = git.run("rev-parse", "FETCH_HEAD^{commit}", cwd=repo).decode().strip()
        if head != commit:
            raise GitError("the fetched commit is not the pinned one")
        yield git, repo, commit
