"""Where Staging containers run: the deployer's Docker host (#765, ADR 0036 decision 1).

A `ContainerDeployer` starts, stops and inspects the container of an image DIGEST. It never builds
and never pulls: the image is the one #867 built on this host, and a digest the host does not have
is `IMAGE_MISSING` (no rebuild, ADR 0032 decision 2).

* `DockerCliDeployer` runs the `docker` client against the daemon the environment names
  (`DOCKER_HOST`), as the image builder does: a list of arguments, no shell, a timeout and a minimal
  environment. Containers join an INTERNAL network (no route to the outside), so the only way out is
  the egress proxy named in `HTTP_PROXY`. Secrets reach the container only through the environment
  of the `docker run` client process (`-e NAME`, no value on the command line), never a file, an
  argument or a log line. Names that could change the client's own behaviour are refused.
* `DisabledDeployer` is the default (`STAGING_RUNTIME=none`): a deployment with no Docker host still
  starts and commits; Staging answers `PENDING_RUNTIME`.
* Tests use a fake with the same protocol.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

from pydantic import SecretStr

from sbam_workers.build.image_builder import DIGEST, KEPT_ENV
from sbam_workers.build.workflow_server import PORT

NAME: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
ENV_NAME: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# Names the docker client or the container's own plumbing read: a secret may not shadow them.
RESERVED_ENV: Final = frozenset(
    set(KEPT_ENV) | {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SBAM_EGRESS_ALLOWLIST", "LD_PRELOAD"}
)
MAX_LOG: Final = 600


class DeployError(Exception):
    """The container could not be started; `code` is stable and the message holds no secret."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class DeploySpec:
    name: str  # the container's name: unique per project and version
    image_digest: str  # `sha256:<64 hex>`, the image id
    env: Mapping[str, SecretStr] = field(repr=False, default_factory=dict)  # injected secrets
    plain_env: Mapping[str, str] = field(default_factory=dict)  # proxy settings: not secret
    network: str = "sbam-staging"


@dataclass(frozen=True)
class Deployed:
    container: str
    endpoint: str  # the workflow server's address on the Staging network


class ContainerDeployer(Protocol):
    name: str

    def start(self, spec: DeploySpec) -> Deployed: ...

    def stop(self, name: str) -> None:
        """Stop the container and keep it (auditable). A container that is not there is fine."""
        ...

    def is_running(self, name: str) -> bool: ...


class DisabledDeployer:
    name = "none"

    def start(self, spec: DeploySpec) -> Deployed:
        raise DeployError("DEPLOYER_DISABLED", "no Staging host is configured (STAGING_RUNTIME)")

    def stop(self, name: str) -> None:
        return None

    def is_running(self, name: str) -> bool:
        return False


class DockerCliDeployer:
    name = "docker"

    def __init__(self, timeout_seconds: int = 120, docker: str = "docker") -> None:
        self.timeout_seconds = timeout_seconds
        self.docker = docker

    def _run(
        self, args: list[str], extra_env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        binary = shutil.which(self.docker)
        if binary is None:
            raise DeployError("DOCKER_MISSING", "no docker client on this host", retryable=True)
        env = {k: v for k, v in os.environ.items() if k in KEPT_ENV}
        env.update(extra_env or {})
        try:
            return subprocess.run(  # noqa: S603 - a list of arguments, no shell
                [binary, *args],
                env=env,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise DeployError(
                "DOCKER_TIMEOUT",
                f"docker did not answer in {self.timeout_seconds}s",
                retryable=True,
            ) from None

    @staticmethod
    def _tail(done: subprocess.CompletedProcess[bytes]) -> str:
        return done.stderr.decode("utf-8", "replace")[-MAX_LOG:].strip()

    def _check(self, done: subprocess.CompletedProcess[bytes], code: str) -> None:
        if done.returncode == 0:
            return
        tail = self._tail(done)
        if "Cannot connect to the Docker daemon" in tail:
            raise DeployError("DAEMON_UNREACHABLE", tail, retryable=True)
        raise DeployError(code, tail or f"docker exited with {done.returncode}")

    def _ensure_network(self, network: str) -> None:
        if self._run(["network", "inspect", network]).returncode == 0:
            return
        self._check(self._run(["network", "create", "--internal", network]), "NETWORK_FAILED")

    def start(self, spec: DeploySpec) -> Deployed:
        if NAME.match(spec.name) is None or NAME.match(spec.network) is None:
            raise DeployError("NAME_INVALID", "the container or network name is not valid")
        if DIGEST.match(spec.image_digest) is None:
            raise DeployError("DIGEST_INVALID", "the image digest is not sha256:<64 hex>")
        for key in spec.env:
            if ENV_NAME.match(key) is None or key.upper() in RESERVED_ENV:
                raise DeployError("ENV_NAME_REFUSED", f"{key!r} cannot be injected as a secret")
        have = self._run(["image", "inspect", "--format", "{{.Id}}", spec.image_digest])
        if have.returncode != 0 or have.stdout.decode().strip() != spec.image_digest:
            self._check(have, "IMAGE_MISSING")
            raise DeployError(
                "IMAGE_MISSING", "this Staging host does not hold the certified image; no rebuild"
            )
        self._ensure_network(spec.network)
        self._run(["rm", "-f", spec.name])  # the same version again replaces its own container
        args = [
            "run",
            "--detach",
            "--name",
            spec.name,
            "--network",
            spec.network,
            "--restart",
            "unless-stopped",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--label",
            "sbam.staging=1",
        ]
        for key, value in spec.plain_env.items():
            args += ["--env", f"{key}={value}"]
        for key in spec.env:
            args += ["--env", key]  # no value: docker reads it from its own environment
        args.append(spec.image_digest)
        injected = {key: value.get_secret_value() for key, value in spec.env.items()}
        self._check(self._run(args, injected), "START_FAILED")
        return Deployed(spec.name, f"http://{spec.name}:{PORT}")

    def stop(self, name: str) -> None:
        if NAME.match(name) is None:
            raise DeployError("NAME_INVALID", "the container name is not valid")
        done = self._run(["stop", name])
        if done.returncode != 0 and "No such container" not in self._tail(done):
            self._check(done, "STOP_FAILED")

    def is_running(self, name: str) -> bool:
        if NAME.match(name) is None:
            return False
        done = self._run(["inspect", "--format", "{{.State.Running}}", name])
        return done.returncode == 0 and done.stdout.decode().strip() == "true"


def make_deployer(kind: str, timeout_seconds: int) -> ContainerDeployer:
    """The deployer named by the worker settings; anything but `docker` runs nothing."""
    if kind == "docker":
        return DockerCliDeployer(timeout_seconds)
    return DisabledDeployer()
