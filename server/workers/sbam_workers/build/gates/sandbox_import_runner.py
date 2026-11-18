"""The import gate's containment backend: the sandbox adapter (#761, ADR 0023).

`ImportGate` needs something that runs the probe of every module where generated code is contained.
`DockerImportRunner` starts a Docker container on the worker's own host. This runner uses the same
backend as the sandbox tests instead (`SandboxBackend`: CubeSandbox when certifying, the local
runner on a laptop), so the import runs in a VM of the pinned skill-runtime template: no egress at
all (`allow_out` is empty), the runtime digest checked from inside, the VM destroyed whatever
happened. One VM serves every module of the bundle; each module still gets a fresh interpreter, so
one module's import cannot hide another's failure.

`ImportGate.run` calls `execute` in a worker thread, so this runner owns its event loop
(`asyncio.run`), like `DockerImportRunner` owns its subprocesses.
"""

import asyncio
import secrets
import uuid
from collections.abc import Mapping
from typing import Final

from sbam_contracts.sandbox import InfraReason, SandboxLimits, normalize_digest, template_alias
from temporalio.exceptions import ApplicationError

from sbam_workers.build.gates.errors import GATE_INPUT_MISMATCH, IMPORT_GATE_INFRA
from sbam_workers.build.gates.import_gate import (
    MODULE_TIMEOUT_S,
    PROBE,
    ImportOutcome,
    check_image,
    outcome_of,
    read_result,
)
from sbam_workers.build.gates.static_tools import PACKAGE
from sbam_workers.sandbox.backend import SandboxBackend, SandboxInfraError, VmHandle, VmSpec
from sbam_workers.sandbox.executor import BOOT_GRACE_S, BUNDLE_DIR, RUNTIME_PATH

PYTHON: Final = "/opt/venv/bin/python"  # the runtime's virtualenv, started by absolute path
MAX_PROBE_OUTPUT: Final = 64 * 1024
LIMITS: Final = SandboxLimits(case_timeout_s=MODULE_TIMEOUT_S)


def probe_argv(nonce: str, module: str) -> tuple[str, ...]:
    """The runtime's PATH restated and the interpreter by absolute path: CubeSandbox goes through a
    login shell that would otherwise replace both (see `executor.RUNNER_ARGV`)."""
    return ("env", f"PATH={RUNTIME_PATH}", PYTHON, "-I", "-B", "-c", PROBE, nonce, module)


def vm_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """The bundle as the package `skill/` below the bundle directory, with its `__init__.py`."""
    root = f"{BUNDLE_DIR}/{PACKAGE}"
    out = {f"{root}/__init__.py": b""}
    for name, data in files.items():
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or "" in parts:
            raise ApplicationError(
                f"unsafe path {name!r}", type=GATE_INPUT_MISMATCH, non_retryable=True
            )
        out[f"{root}/{name}"] = data
    return out


class SandboxImportRunner:
    """`ImportRunner` on a `SandboxBackend`. Trouble with the VM is `IMPORT_GATE_INFRA`: retried,
    never a correction and never recorded as a verdict."""

    def __init__(
        self,
        backend: SandboxBackend,
        *,
        environment: str = "development",
        timeout_s: int = MODULE_TIMEOUT_S,
    ) -> None:
        self.backend = backend
        self.environment = environment
        self.timeout_s = timeout_s

    def run(
        self, files: Mapping[str, bytes], modules: list[str], image: str
    ) -> list[ImportOutcome]:
        check_image(image)  # a tag is refused before anything else
        digest = image.rsplit("@", 1)[1]
        try:
            return asyncio.run(self._run(vm_files(files), modules, normalize_digest(digest)))
        except SandboxInfraError as exc:
            raise ApplicationError(str(exc), type=IMPORT_GATE_INFRA) from exc

    async def _run(
        self, files: Mapping[str, bytes], modules: list[str], digest: str
    ) -> list[ImportOutcome]:
        run_id = uuid.uuid4()
        spec = VmSpec(
            run_id=run_id,
            template_alias=template_alias(digest),
            runtime_digest=digest,
            limits=LIMITS,
            allow_out=(),  # an import needs no network: any attempt is a finding, not a connection
            hosts={},
            ttl_seconds=self.timeout_s * max(1, len(modules)) + BOOT_GRACE_S,
            metadata={"sbam_import_gate": str(run_id), "environment": self.environment},
        )
        vm = await self.backend.create(spec)
        try:
            return await self._probe(vm, files, modules, digest)
        finally:  # shielded: a cancel must not leave a VM to the reaper
            await asyncio.shield(self.backend.destroy(vm.vm_id))

    async def _probe(
        self, vm: VmHandle, files: Mapping[str, bytes], modules: list[str], digest: str
    ) -> list[ImportOutcome]:
        seen = await self.backend.read_runtime_digest(vm)
        try:
            same = normalize_digest(seen.strip()) == digest
        except ValueError:
            same = False
        if not same:
            raise SandboxInfraError(
                InfraReason.TEMPLATE_DIGEST_MISMATCH, "the VM runs another runtime"
            )
        await self.backend.put_files(vm, files)
        outcomes: list[ImportOutcome] = []
        for module in modules:
            nonce = secrets.token_hex(8)
            done = await self.backend.exec(
                vm,
                probe_argv(nonce, module),
                timeout_s=float(self.timeout_s),
                env={"SBAM_IMPORT_ROOT": BUNDLE_DIR},
                max_output_bytes=MAX_PROBE_OUTPUT,
            )
            if done.timed_out:
                message = f"no result after {self.timeout_s}s"
                outcomes.append(ImportOutcome(module, False, "", message, (), True))
                continue
            stdout = done.stdout.decode("utf-8", "replace")
            outcomes.append(outcome_of(module, read_result(stdout, nonce), done.exit_code))
        return outcomes
