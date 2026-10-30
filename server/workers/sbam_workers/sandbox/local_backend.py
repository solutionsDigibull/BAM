"""The non-certifying backend: talks to the `sandbox-runner` agent over HTTP (ADR 0023, decision 6).

The worker never touches a docker socket. It holds a bearer token for the agent on the internal
`sbam_sandbox` network and nothing else. Isolation is a private directory, resource limits and a
container with no capabilities: weaker than a microVM, so `mode` is `LOCAL_DOCKER_NONCERT` and no
result of this backend can certify.

Rules worth knowing
  * A transport failure, a 429 or a 5xx is `SandboxInfraError`; a 4xx for a request we built is a
    bug in us and is infrastructure too (never a test failure).
  * `destroy` treats "already gone" as success, like the protocol says.
"""

import base64
from collections.abc import Mapping, Sequence
from typing import Any, Final

import httpx
from sbam_contracts.sandbox import InfraReason, SandboxMode

from sbam_workers.sandbox.backend import (
    ExecResult,
    SandboxInfraError,
    VmHandle,
    VmInfo,
    VmSpec,
)

CONTROL_TIMEOUT_S: Final = 30.0
EXEC_SLACK_S: Final = 30.0  # the agent enforces the run's timeout; the client waits a bit longer


class LocalDockerBackend:
    mode = SandboxMode.LOCAL_DOCKER_NONCERT

    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=CONTROL_TIMEOUT_S,
        )

    async def _call(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout: float = CONTROL_TIMEOUT_S,
        reason: InfraReason = InfraReason.HOST_UNREACHABLE,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=body, timeout=timeout)
        except httpx.TransportError:
            raise SandboxInfraError(
                InfraReason.HOST_UNREACHABLE, "the sandbox agent is down"
            ) from None
        if response.status_code == 404 and method == "DELETE":
            return {}
        if response.status_code >= 400:
            raise SandboxInfraError(reason, f"the sandbox agent answered {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise SandboxInfraError(
                reason, "the sandbox agent answered with a non-JSON body"
            ) from None
        if not isinstance(payload, dict):
            raise SandboxInfraError(reason, "the sandbox agent answered with an unexpected shape")
        return payload

    async def create(self, spec: VmSpec) -> VmHandle:
        body = {"run_id": str(spec.run_id), "ttl_seconds": spec.ttl_seconds}
        made = await self._call("POST", "/v1/vms", body, reason=InfraReason.VM_CREATE_FAILED)
        vm_id = made.get("vm_id")
        if not isinstance(vm_id, str):
            raise SandboxInfraError(InfraReason.VM_CREATE_FAILED, "no vm id in the answer")
        return VmHandle(vm_id)

    async def read_runtime_digest(self, vm: VmHandle) -> str:
        answer = await self._call("GET", "/v1/runtime")
        return str(answer.get("digest", ""))

    async def put_files(
        self, vm: VmHandle, files: Mapping[str, bytes], *, system: bool = False
    ) -> None:
        encoded = {path: base64.b64encode(data).decode() for path, data in files.items()}
        await self._call("PUT", f"/v1/vms/{vm.vm_id}/files", {"files": encoded})

    async def exec(
        self,
        vm: VmHandle,
        argv: Sequence[str],
        *,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int,
    ) -> ExecResult:
        body = {
            "argv": list(argv),
            "timeout_s": timeout_s,
            "env": dict(env or {}),
            "max_output_bytes": max_output_bytes,
        }
        answer = await self._call(
            "POST",
            f"/v1/vms/{vm.vm_id}/exec",
            body,
            timeout=timeout_s + EXEC_SLACK_S,
            reason=InfraReason.RUNNER_FAILED,
        )
        try:
            return ExecResult(
                exit_code=int(answer["exit_code"]),
                stdout=base64.b64decode(answer["stdout"]),
                stderr=base64.b64decode(answer["stderr"]),
                truncated=bool(answer["truncated"]),
                timed_out=bool(answer["timed_out"]),
            )
        except (KeyError, ValueError, TypeError):
            raise SandboxInfraError(InfraReason.RUNNER_FAILED, "malformed exec answer") from None

    async def get_file(self, vm: VmHandle, path: str, *, max_bytes: int) -> bytes | None:
        answer = await self._call(
            "POST",
            f"/v1/vms/{vm.vm_id}/file",
            {"path": path, "max_bytes": max_bytes},
            reason=InfraReason.RUNNER_FAILED,
        )
        if not answer.get("found"):
            return None
        try:
            return base64.b64decode(str(answer.get("data", "")), validate=True)
        except ValueError:
            raise SandboxInfraError(InfraReason.RUNNER_FAILED, "malformed file answer") from None

    async def destroy(self, vm_id: str) -> None:
        await self._call("DELETE", f"/v1/vms/{vm_id}", reason=InfraReason.DESTROY_FAILED)

    async def list_vms(self) -> list[VmInfo]:
        answer = await self._call("GET", "/v1/vms")
        out = []
        for item in answer.get("vms", []):
            run_id = str(item.get("run_id", ""))
            out.append(VmInfo(str(item["vm_id"]), None, {"sbam_run": run_id}))
        return out
