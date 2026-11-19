"""Building the Workflow server image (#867, ADR 0028 decision 3).

An `ImageBuilder` takes the Docker build context (the stored server files: Dockerfile, vendored
repository trees, generated skills, `server.py`) and gives back the digest of the image it built.
Nothing here calls a model, and the build context is the only input: no file of the host goes in.

* `DockerCliBuilder` runs `docker build` on a scratch copy of the context against the Docker
  daemon the environment names (`DOCKER_HOST`). The worker holds no socket by default (the
  sandbox and toolchain precedent), so this is for a build host that has one: a developer machine
  or the Staging deployer's host. `docker build` is the only command it runs, with a list of
  arguments and no shell, a timeout and a minimal environment.
* `DisabledBuilder` is the default: the stage records `BUILDER_DISABLED` and goes on, so a
  deployment without a build host builds everything else exactly as before.
* Tests use a fake that implements the same protocol.

The digest is the image id (`sha256:<64 hex>`: the digest of the image config, which names the
layers), the same value `docker run` and `docker image inspect` accept. A registry push and its
repo digest are a later step of the deployer (#765).
"""

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
TAG: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}(:[A-Za-z0-9_.-]{1,128})?$")
KEPT_ENV: Final = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT", "SYSTEMROOT")
MAX_LOG: Final = 2000


class ImageBuildError(Exception):
    """The image could not be built; `code` is stable. `retryable` says whether the same context
    may give a different answer later (the daemon was away) or never will (the build failed)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class BuiltImage:
    digest: str  # `sha256:<64 hex>`


class ImageBuilder(Protocol):
    name: str

    def build(self, files: Mapping[str, bytes], tag: str) -> BuiltImage: ...


class DisabledBuilder:
    name = "none"

    def build(self, files: Mapping[str, bytes], tag: str) -> BuiltImage:
        raise ImageBuildError("BUILDER_DISABLED", "no image build host is configured")


def _write(root: Path, files: Mapping[str, bytes]) -> None:
    for path, data in files.items():
        target = (root / path).resolve()
        if root.resolve() not in target.parents:  # a path out of the context is never written
            raise ImageBuildError("CONTEXT_INVALID", f"{path!r} is outside the build context")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class DockerCliBuilder:
    name = "docker"

    def __init__(self, timeout_seconds: int = 900, docker: str = "docker") -> None:
        self.timeout_seconds = timeout_seconds
        self.docker = docker

    def build(self, files: Mapping[str, bytes], tag: str) -> BuiltImage:
        if TAG.match(tag) is None:
            raise ImageBuildError("TAG_INVALID", f"{tag!r} is not an image tag")
        if "Dockerfile" not in files:
            raise ImageBuildError("CONTEXT_INVALID", "the build context has no Dockerfile")
        binary = shutil.which(self.docker)
        if binary is None:
            raise ImageBuildError("DOCKER_MISSING", "no docker client on this host", retryable=True)
        env = {k: v for k, v in os.environ.items() if k in KEPT_ENV}
        with tempfile.TemporaryDirectory(prefix="sbam-image-") as scratch:
            root = Path(scratch) / "context"
            root.mkdir()
            _write(root, files)
            iid = Path(scratch) / "image.id"
            command = [binary, "build", "--tag", tag, "--iidfile", str(iid), "--quiet", str(root)]
            try:
                done = subprocess.run(  # noqa: S603 - a list of arguments, no shell
                    command,
                    env=env,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise ImageBuildError(
                    "BUILD_TIMEOUT", f"no image after {self.timeout_seconds}s", retryable=True
                ) from None
            if done.returncode != 0:
                tail = done.stderr.decode("utf-8", "replace")[-MAX_LOG:].strip()
                daemon_away = "Cannot connect to the Docker daemon" in tail
                raise ImageBuildError(
                    "DAEMON_UNREACHABLE" if daemon_away else "BUILD_FAILED",
                    tail or f"docker exited with {done.returncode}",
                    retryable=daemon_away,
                )
            digest = iid.read_text().strip() if iid.exists() else ""
        if DIGEST.match(digest) is None:
            raise ImageBuildError("DIGEST_UNREADABLE", f"docker gave {digest[:80]!r} as image id")
        return BuiltImage(digest)


def make_builder(kind: str, timeout_seconds: int) -> ImageBuilder:
    """The builder named by the worker settings; anything but `docker` builds nothing."""
    if kind == "docker":
        return DockerCliBuilder(timeout_seconds)
    return DisabledBuilder()
