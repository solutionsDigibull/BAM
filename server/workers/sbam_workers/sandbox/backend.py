"""The one interface the executor knows (ADR 0021, decision 1).

A backend creates an isolated environment, puts files in it, runs fixed commands as the
unprivileged sandbox user, reads files back and destroys it. `CubeSandboxBackend` (certifying) and
`LocalDockerBackend` (never certifying) implement it; tests use `FakeBackend`.

Rules worth knowing
  * `exec` takes an argument vector, never a shell string. Backends that have to go through a shell
    (the CubeSandbox SDK runs `bash -l -c`) quote every element themselves.
  * `destroy` is idempotent: destroying a VM that is already gone succeeds.
  * Infrastructure trouble is `SandboxInfraError` with an `InfraReason`. Everything else a backend
    raises is a bug and is treated as infrastructure too, but never as a test failure.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sbam_contracts.sandbox import InfraReason, SandboxLimits, SandboxMode


class SandboxInfraError(Exception):
    """The sandbox, not the code under test, is the problem: retried, never a correction."""

    def __init__(self, reason: InfraReason, message: str = "") -> None:
        super().__init__(f"{reason.value}: {message}" if message else reason.value)
        self.reason = reason


@dataclass(frozen=True)
class VmSpec:
    run_id: uuid.UUID
    template_alias: str  # `template_alias(runtime_digest)`; the backend resolves and checks it
    runtime_digest: str  # `sha256:<hex>`, normalised
    limits: SandboxLimits
    allow_out: tuple[str, ...]  # validated IPv4 addresses, the only egress the VM gets
    hosts: Mapping[str, tuple[str, ...]]  # name -> IPv4s written to the VM's hosts file (no DNS)
    ttl_seconds: int  # the host's own backstop: it kills the VM after this, whatever we do
    metadata: Mapping[str, str]  # tags the reaper filters on (run, environment, deadline)


@dataclass(frozen=True)
class VmHandle:
    vm_id: str


@dataclass(frozen=True)
class VmInfo:
    vm_id: str
    started_at: datetime | None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    truncated: bool = False  # output was cut at the limit
    timed_out: bool = False  # the deadline passed and the process was killed


class SandboxBackend(Protocol):
    mode: SandboxMode

    async def create(self, spec: VmSpec) -> VmHandle: ...

    async def read_runtime_digest(self, vm: VmHandle) -> str:
        """What the VM says it is: the contents of `/etc/sbam-runtime`."""
        ...

    async def put_files(
        self, vm: VmHandle, files: Mapping[str, bytes], *, system: bool = False
    ) -> None:
        """Write absolute-path files. `system=True` writes as root, for platform files only."""
        ...

    async def exec(
        self,
        vm: VmHandle,
        argv: Sequence[str],
        *,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int,
    ) -> ExecResult: ...

    async def get_file(self, vm: VmHandle, path: str, *, max_bytes: int) -> bytes | None:
        """The file's bytes, or None when it does not exist. Larger than `max_bytes`: error."""
        ...

    async def destroy(self, vm_id: str) -> None: ...

    async def list_vms(self) -> list[VmInfo]:
        """Every VM on the host, with its metadata tags (for the reaper)."""
        ...
