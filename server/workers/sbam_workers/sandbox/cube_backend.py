"""The certifying backend: CubeSandbox microVMs through the official SDK (ADR 0023, d.2, 3, 4).

Two planes, one SDK: control (`create`, `kill`, `list`) is REST on CubeAPI; data (`exec`, files) is
Connect RPC to the guest's `envd` through CubeProxy. The SDK is synchronous and its REST calls carry
no timeout, so every call runs in a worker thread under `asyncio.wait_for`.

Rules worth knowing
  * Tests run as the unprivileged `sbam` user, never as root. Only `system=True` file writes (the
    hosts file) are root's. The SDK runs `bash -l -c <cmd>`, so every argv element is quoted here
    and nothing else is ever part of the command text.
  * Output goes to files inside the VM and is read back through `head -c`, so a case that prints
    gigabytes cannot fill the worker's memory (the SDK reads a whole stream into one string) and no
    tail is lost to a pipe that is still draining when the process ends.
  * Egress is the allowlist the planner pinned (IPv4 only) with internet access off. CubeSandbox's
    precedence is allow > deny > default, and its L3/L4 drops are not logged; evidence comes from
    `egress_reader`.
  * `destroy` of a VM that is already gone succeeds.
"""

import asyncio
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, TypeVar

import httpx
from sbam_contracts.sandbox import InfraReason, SandboxMode

from sbam_workers.sandbox.backend import (
    ExecResult,
    SandboxInfraError,
    VmHandle,
    VmInfo,
    VmSpec,
)

T = TypeVar("T")

RUN_USER: Final = "sbam"  # uid 10001 in the template (host kit)
SYSTEM_USER: Final = "root"
CONTROL_TIMEOUT_S: Final = 30.0
EXEC_SLACK_S: Final = 30.0  # envd enforces the run's deadline; the client waits a bit longer
TIMEOUT_EXIT_CODE: Final = 124
RUNTIME_DIGEST_PATH: Final = "/etc/sbam-runtime"
STDOUT_PATH: Final = "/tmp/.sbam-stdout"  # noqa: S108 - inside the VM, written by the run user
STDERR_PATH: Final = "/tmp/.sbam-stderr"  # noqa: S108


@dataclass(frozen=True)
class CubeSettings:
    """The only host-specific inputs (`SANDBOX_MODE=CUBESANDBOX`, `CUBE_*`)."""

    api_url: str
    proxy_node_ip: str | None = None
    proxy_port: int = 80
    sandbox_domain: str = "cube.app"
    api_key: str | None = field(default=None, repr=False)


def _sdk() -> Any:
    import cubesandbox  # type: ignore[import-untyped]  # the SDK ships no stubs

    return cubesandbox


def redirected_command(argv: Sequence[str]) -> str:
    """`argv` as one shell line that writes each stream to a file and keeps the command's exit
    code."""
    return f"{shlex.join(argv)} > {STDOUT_PATH} 2> {STDERR_PATH}"


def tail_command(path: str, max_output_bytes: int) -> str:
    """At most one byte more than the cap, so the reader can tell "exactly the cap" from "cut"."""
    return f"head -c {int(max_output_bytes) + 1} {path}"


class CubeSandboxBackend:
    mode = SandboxMode.CUBESANDBOX

    def __init__(self, settings: CubeSettings, sdk: Any | None = None) -> None:
        self._sdk_module = sdk if sdk is not None else _sdk()
        self._config = self._sdk_module.Config(
            api_url=settings.api_url,
            api_key=settings.api_key,
            proxy_node_ip=settings.proxy_node_ip,
            proxy_port=settings.proxy_port,
            sandbox_domain=settings.sandbox_domain,
        )
        self._live: dict[str, Any] = {}

    # --- plumbing ------------------------------------------------------------------------------

    async def _call(
        self,
        fn: Callable[[], T],
        reason: InfraReason,
        *,
        timeout: float | None = None,
        missing_ok: bool = False,
    ) -> T:
        """Run one blocking SDK call. Anything the sandbox side did wrong is `SandboxInfraError`."""
        sdk = self._sdk_module
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout or CONTROL_TIMEOUT_S)
        except SandboxInfraError:
            raise
        except TimeoutError:
            raise SandboxInfraError(
                InfraReason.HOST_UNREACHABLE, "the sandbox host timed out"
            ) from None
        except sdk.TemplateNotFoundError:
            raise SandboxInfraError(
                InfraReason.TEMPLATE_DIGEST_MISMATCH, "no template for this runtime digest"
            ) from None
        except sdk.SandboxNotFoundError:
            if missing_ok:
                return None  # type: ignore[return-value]
            raise SandboxInfraError(reason, "the sandbox is gone") from None
        except sdk.CubeSandboxError as exc:
            raise SandboxInfraError(
                reason, f"the sandbox host answered {exc.status_code}"
            ) from None
        except (httpx.TransportError, OSError, ConnectionError) as exc:
            # requests' ConnectionError is an OSError; so is a refused socket
            raise SandboxInfraError(
                InfraReason.HOST_UNREACHABLE,
                f"the sandbox host is unreachable ({type(exc).__name__})",
            ) from None
        except RuntimeError as exc:
            raise SandboxInfraError(reason, f"the sandbox failed: {type(exc).__name__}") from None

    def _vm(self, vm: VmHandle) -> Any:
        try:
            return self._live[vm.vm_id]
        except KeyError:
            raise SandboxInfraError(
                InfraReason.RUNNER_FAILED, "this worker does not hold the VM"
            ) from None

    # --- the protocol --------------------------------------------------------------------------

    async def create(self, spec: VmSpec) -> VmHandle:
        sdk = self._sdk_module
        metadata = {**spec.metadata, "sbam_ttl_s": str(spec.ttl_seconds)}

        def make() -> Any:
            return sdk.Sandbox.create(
                spec.template_alias,
                timeout=spec.ttl_seconds,
                metadata=metadata,
                allow_internet_access=False,
                network={"allow_out": list(spec.allow_out)} if spec.allow_out else None,
                config=self._config,
            )

        box = await self._call(make, InfraReason.VM_CREATE_FAILED, timeout=CONTROL_TIMEOUT_S * 4)
        try:
            await self._within_limits(box, spec)
        except BaseException:  # a VM this run may not use must not outlive the refusal
            await asyncio.shield(self.destroy(box.sandbox_id, known=box))
            raise
        self._live[box.sandbox_id] = box
        return VmHandle(box.sandbox_id)

    async def _within_limits(self, box: Any, spec: VmSpec) -> None:
        """The template, not this call, decides a VM's size (the SDK has no cpu or memory). A
        template bigger than the run's limits would silently void them, so it is refused. A host that
        reports no size is believed: only a size it states can be wrong."""
        info = await self._call(box.get_info, InfraReason.VM_CREATE_FAILED)
        memory, cpus = getattr(info, "memory_mb", None), getattr(info, "cpu_count", None)
        if (memory is not None and memory > spec.limits.memory_mb) or (
            cpus is not None and cpus > spec.limits.cpu
        ):
            raise SandboxInfraError(
                InfraReason.VM_CREATE_FAILED, "the template's resources exceed the run's limits"
            )

    async def read_runtime_digest(self, vm: VmHandle) -> str:
        box = self._vm(vm)
        text = await self._call(
            lambda: box.files.read(RUNTIME_DIGEST_PATH, user=SYSTEM_USER), InfraReason.RUNNER_FAILED
        )
        return str(text)

    async def put_files(
        self, vm: VmHandle, files: Mapping[str, bytes], *, system: bool = False
    ) -> None:
        box = self._vm(vm)
        user = SYSTEM_USER if system else RUN_USER
        for path, data in files.items():
            await self._call(
                lambda p=path, d=data: box.files.write(p, d, user=user),  # type: ignore[misc]
                InfraReason.RUNNER_FAILED,
            )

    async def exec(
        self,
        vm: VmHandle,
        argv: Sequence[str],
        *,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int,
    ) -> ExecResult:
        box = self._vm(vm)
        envs = dict(env or {})

        def run(line: str, deadline: float) -> Any:
            """The command's result, or None when its deadline passed (the process is the VM's to
            kill)."""
            try:
                return box.commands.run(line, timeout=deadline, envs=envs, user=RUN_USER)
            except httpx.TimeoutException:
                return None
            except RuntimeError as exc:
                if str(exc).startswith("deadline_exceeded"):
                    return None
                raise

        result = await self._call(
            lambda: run(redirected_command(argv), timeout_s),
            InfraReason.RUNNER_FAILED,
            timeout=timeout_s + EXEC_SLACK_S,
        )
        timed_out = result is None
        streams: list[bytes] = []
        for path in (STDOUT_PATH, STDERR_PATH):
            tail = await self._call(
                lambda p=path: run(tail_command(p, max_output_bytes), CONTROL_TIMEOUT_S),  # type: ignore[misc]
                InfraReason.RUNNER_FAILED,
            )
            streams.append(tail.stdout.encode("utf-8", "replace") if tail is not None else b"")
        truncated = any(len(stream) > max_output_bytes for stream in streams)
        stdout, stderr = (stream[:max_output_bytes] for stream in streams)
        return ExecResult(
            exit_code=TIMEOUT_EXIT_CODE if result is None else int(result.exit_code),
            stdout=stdout,
            stderr=stderr,
            truncated=truncated,
            timed_out=timed_out,
        )

    async def get_file(self, vm: VmHandle, path: str, *, max_bytes: int) -> bytes | None:
        box = self._vm(vm)
        sdk = self._sdk_module

        def read() -> bytes | None:
            if not box.files.exists(path, user=RUN_USER):
                return None
            text: str = box.files.read(path, user=RUN_USER)
            return text.encode("utf-8")

        try:
            data = await self._call(read, InfraReason.RUNNER_FAILED)
        except sdk.FilesystemNotFoundError:  # pragma: no cover - exists() handles it
            return None
        if data is not None and len(data) > max_bytes:
            raise SandboxInfraError(InfraReason.RUNNER_FAILED, "the file is larger than the limit")
        return data

    async def destroy(self, vm_id: str, *, known: Any = None) -> None:
        sdk = self._sdk_module
        box = self._live.pop(vm_id, None) or known

        def kill() -> None:
            target = box if box is not None else sdk.Sandbox.connect(vm_id, config=self._config)
            target.kill()

        await self._call(kill, InfraReason.DESTROY_FAILED, missing_ok=True)

    async def list_vms(self) -> list[VmInfo]:
        sdk = self._sdk_module
        rows = await self._call(
            lambda: sdk.Sandbox.list_v2(config=self._config), InfraReason.HOST_UNREACHABLE
        )
        out: list[VmInfo] = []
        for row in rows:
            # the SDK's own parser: CubeAPI gives ISO strings or epoch seconds, and a start time we
            # cannot read would leave the VM unreapable for ever
            info = sdk.SandboxInfo.from_dict(row)
            out.append(VmInfo(str(row["sandboxID"]), info.started_at, dict(info.metadata or {})))
        return out
