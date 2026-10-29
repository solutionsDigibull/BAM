"""The `import_gate` build stage (E12.5): every module of a bundle imports, with no side effects.

Unlike every other gate this one *executes* generated code (an import runs module top level), so it
runs only where the code is contained. `DockerImportRunner` starts one container per module from
the exact `skill-runtime` image digest the build pinned (`name@sha256:...`, never a tag): no
network, read-only root and bundle, non-root, no capabilities, CPU, memory and pid limits, and a
60 s timeout after which the container is removed. `UncontainedImportRunner` runs the same probe
on this host: tests with trusted modules only.

The probe imports the module with `importlib` as part of the `skill` package (the bundle uses
relative imports) in a fresh `python -I -B`, with a `sys.addaudithook` that records and then blocks
sockets, subprocesses, process replacement and file writes or deletes. The probe reports through one
marker line carrying a nonce. That is an honest self-report: code that gets past the AST gate could
forge it, which is why the container, not the probe, is the boundary and why this gate is a build
check (a missing library, a typo, a cycle), not a security control.

Preconditions (a wiring defect, non-retryable, nothing is run): the AST gates and the static gate
all PASSED on this very `bundle_sha256`. Findings (gate IMPORT_RESOLUTION): `ImportError`,
`ModuleNotFoundError`, `SyntaxError` (what the import raised), `IMPORT_ERROR` (anything else it
raised, or the interpreter died), `IMPORT_SIDE_EFFECT`, `IMPORT_TIMEOUT`. They go to the corrector
like any gate failure. A container that cannot be started is `IMPORT_GATE_INFRA`: retryable, never a
correction, recorded as nothing.

Not registered yet: `SubSkillBuild` has no import stage, and the worker needs a containment backend
(the sandbox adapter, E13.2) before it may run this. Whoever wires it registers
`ImportGate(...).run`.
"""

import asyncio
import hashlib
import json
import re
import secrets
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Protocol

from sbam_api.db.repository import TenantContext
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.storage.base import StorageBackend
from sbam_contracts.build import GATE_FAILED, BuildStageInput
from sbam_contracts.build_dashboard import GateName, GateStatus, Severity
from sbam_contracts.pipeline import IdResult
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.build.gates.ast_gate import GATES as AST_GATES
from sbam_workers.build.gates.errors import GATE_INPUT_MISMATCH, IMPORT_GATE_INFRA
from sbam_workers.build.gates.static_gate import GATES as STATIC_GATES
from sbam_workers.build.gates.static_tools import PACKAGE, finding
from sbam_workers.build.gates.stored_bundle import (
    StoredBundle,
    Verdict,
    fail,
    find_assembled,
    read_verified,
    record_verdicts,
)

STAGE = "import_gate"
GATE_VERSION: Final = "import-gate/1.0.0"
MODULE_TIMEOUT_S: Final = 60
MARKER: Final = "SBAM-IMPORT-RESULT"
IMPORT_ERRORS: Final = frozenset({"ImportError", "ModuleNotFoundError", "SyntaxError"})
# `name@sha256:<64 hex>`: an exact digest, never a tag (a tag moves under a pinned build).
PINNED_IMAGE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
MAX_ERROR: Final = 300

PROBE: Final = r"""
import importlib, json, os, sys
nonce, module = sys.argv[1], sys.argv[2]
del sys.argv[1:]
sys.path.insert(0, os.getcwd())
events = []
WATCH = {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen", "os.system",
         "os.exec", "os.fork", "os.forkpty", "os.posix_spawn", "os.spawn", "os.remove",
         "os.rename", "os.rmdir", "os.mkdir", "os.symlink", "os.link", "os.truncate",
         "shutil.rmtree", "shutil.copyfile", "shutil.move", "ctypes.dlopen", "ctypes.dlsym",
         "ctypes.cdata", "pty.spawn", "os.putenv", "os.unsetenv"}
def hook(event, args):
    blocked = event in WATCH
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        write = isinstance(mode, str) and any(c in mode for c in "wax+")
        if write or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)):
            events.append("open:write")
            raise PermissionError("blocked by the import gate: file write")
    if blocked:
        events.append(event)
        raise PermissionError("blocked by the import gate: " + event)
sys.addaudithook(hook)
result = {"ok": True, "type": "", "error": ""}
try:
    importlib.import_module("skill." + module)
except BaseException as exc:
    result = {"ok": False, "type": type(exc).__name__, "error": str(exc)[:300]}
result["events"] = sorted(set(events))
sys.stdout.write("\n" + nonce + " " + json.dumps(result) + "\n")
sys.stdout.flush()
"""
CONFIG_SHA256: Final = "sha256:" + hashlib.sha256((GATE_VERSION + PROBE).encode()).hexdigest()


def module_names(files: Mapping[str, bytes]) -> list[str]:
    """Dotted name of every .py file below the package, sorted; `pkg/__init__.py` is `pkg`."""
    names: list[str] = []
    for name in files:
        if not name.endswith(".py"):
            continue
        parts = list(PurePosixPath(name).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            names.append(".".join(parts))
    return sorted(names)


@dataclass(frozen=True)
class ImportOutcome:
    module: str
    ok: bool
    error_type: str = ""
    error: str = ""
    events: tuple[str, ...] = ()
    timed_out: bool = False


class ImportRunner(Protocol):
    def run(
        self, files: Mapping[str, bytes], modules: list[str], image: str
    ) -> list[ImportOutcome]: ...


def check_image(image: str) -> str:
    """The image must be pinned by digest: refuse a tag (`latest`) rather than run on whatever it
    points at today. A wiring defect, so non-retryable."""
    if not PINNED_IMAGE.fullmatch(image):
        raise ApplicationError(
            "the skill-runtime image must be pinned by sha256 digest",
            type=GATE_INPUT_MISMATCH,
            non_retryable=True,
        )
    return image


def write_package(root: Path, files: Mapping[str, bytes]) -> Path:
    """The bundle as the package `skill/` below `root`, readable by any user (the container runs as
    nobody) and containing nothing but the bundle's files."""
    package = root / PACKAGE
    package.mkdir(parents=True)
    for name, data in files.items():
        target = package / name
        if package not in target.resolve().parents:
            raise ApplicationError(f"unsafe path {name!r}", type=GATE_INPUT_MISMATCH)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (package / "__init__.py").touch(exist_ok=True)
    for path in [root, *root.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    return root


def read_result(stdout: str, nonce: str) -> dict[str, Any] | None:
    """The probe's marker line (the last one), or None: the interpreter never got to report."""
    lines = [ln for ln in stdout.splitlines() if ln.startswith(nonce + " ")]
    try:
        row: dict[str, Any] = json.loads(lines[-1][len(nonce) + 1 :])
        row["ok"], row["type"], row["error"], row["events"]
        return row
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def outcome_of(module: str, row: dict[str, Any] | None, returncode: int) -> ImportOutcome:
    if row is None:  # the module killed the interpreter (os._exit, OOM, a signal) before the report
        error = f"the interpreter exited with {returncode} without reporting"
        return ImportOutcome(module, False, "InterpreterExit", error)
    return ImportOutcome(
        module,
        bool(row["ok"]),
        str(row["type"])[:60],
        str(row["error"])[:MAX_ERROR],
        tuple(str(e) for e in row["events"]),
    )


class UncontainedImportRunner:
    """Runs the probe on this host: for tests with trusted modules, never for generated code."""

    def __init__(self, python: str, timeout_s: int = MODULE_TIMEOUT_S) -> None:
        self.python = python
        self.timeout_s = timeout_s

    def run(
        self, files: Mapping[str, bytes], modules: list[str], image: str
    ) -> list[ImportOutcome]:
        with tempfile.TemporaryDirectory(prefix="sbam-import-") as tmp:
            root = write_package(Path(tmp) / "b", files)
            return [self._one(root, m) for m in modules]

    def _one(self, root: Path, module: str) -> ImportOutcome:
        nonce = secrets.token_hex(8)
        argv = [self.python, "-I", "-B", "-c", PROBE, nonce, module]
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv; trusted test modules only
                argv, cwd=root, capture_output=True, text=True, timeout=self.timeout_s, check=False
            )
        except subprocess.TimeoutExpired:
            return ImportOutcome(module, False, "", f"no result after {self.timeout_s}s", (), True)
        except OSError as exc:
            raise ApplicationError(f"cannot start python: {exc}", type=IMPORT_GATE_INFRA) from exc
        return outcome_of(module, read_result(done.stdout, nonce), done.returncode)


def docker_argv(
    image: str, name: str, root: Path, nonce: str, module: str, python: str = "python"
) -> list[str]:
    """The container the import runs in (see the module docstring); every flag is tested."""
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--read-only",
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "512m", "--memory-swap", "512m",
        "--cpus", "1",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",  # noqa: S108 - the container's own tmpfs
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--volume", f"{root}:/bundle:ro",
        "--workdir", "/bundle",
        "--entrypoint", python,
        check_image(image),
        "-I", "-B", "-c", PROBE, nonce, module,
    ]  # fmt: skip


class DockerImportRunner:
    def __init__(
        self, timeout_s: int = MODULE_TIMEOUT_S, docker: str = "docker", python: str = "python"
    ) -> None:
        self.timeout_s = timeout_s
        self.docker = docker
        self.python = python  # on the image's PATH (the venv with the allowed libraries)

    def run(
        self, files: Mapping[str, bytes], modules: list[str], image: str
    ) -> list[ImportOutcome]:
        check_image(image)
        with tempfile.TemporaryDirectory(prefix="sbam-import-") as tmp:
            root = write_package(Path(tmp) / "b", files)
            return [self._one(image, root, m) for m in modules]

    def _remove(self, name: str) -> None:
        subprocess.run(  # noqa: S603 - fixed argv
            [self.docker, "rm", "-f", name], capture_output=True, timeout=30, check=False
        )

    def _one(self, image: str, root: Path, module: str) -> ImportOutcome:
        nonce, name = secrets.token_hex(8), f"sbam-import-{uuid.uuid4().hex[:12]}"
        argv = docker_argv(image, name, root, nonce, module, self.python)
        argv[0] = self.docker
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, never a shell
                argv, capture_output=True, text=True, timeout=self.timeout_s, check=False
            )
        except subprocess.TimeoutExpired:
            self._remove(name)  # killing the client would leave the container running
            return ImportOutcome(module, False, "", f"no result after {self.timeout_s}s", (), True)
        except OSError as exc:
            raise ApplicationError(f"cannot start docker: {exc}", type=IMPORT_GATE_INFRA) from exc
        if done.returncode in (
            125,
            126,
            127,
        ):  # docker itself failed: the daemon, the image, a flag
            detail = (done.stderr or "").strip()[:200]
            raise ApplicationError(
                f"docker could not run the import: {detail}", type=IMPORT_GATE_INFRA
            )
        return outcome_of(module, read_result(done.stdout, nonce), done.returncode)


def import_findings(outcomes: list[ImportOutcome]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    gate, high = GateName.IMPORT_RESOLUTION, Severity.HIGH
    for o in outcomes:
        file = o.module.replace(".", "/") + ".py"
        if o.timed_out:
            found.append(finding(gate, "IMPORT_TIMEOUT", file, None, high, o.error))
        elif not o.ok:
            code = o.error_type if o.error_type in IMPORT_ERRORS else "IMPORT_ERROR"
            message = f"{o.error_type}: {o.error}" if o.error_type else o.error
            found.append(finding(gate, code, file, None, high, f"{o.module}: {message}"))
        for event in o.events:
            message = f"importing {o.module} caused a side effect: {event}"
            found.append(finding(gate, "IMPORT_SIDE_EFFECT", file, None, high, message))
    return sorted(found, key=lambda f: (f["file"], f["code"], f["message"]))


class ImportGate:
    """`import_gate` with its dependencies injected (see the module docstring). `image_for` gives
    the skill-runtime image digest the build pinned (`runtime_image_digest` of its manifest)."""

    def __init__(
        self,
        storage: StorageBackend,
        sessions: sessionmaker[Session],
        runner: ImportRunner,
        image_for: Callable[[BuildStageInput], str],
    ) -> None:
        self.storage = storage
        self.sessions = sessions
        self.runner = runner
        self.image_for = image_for

    def _require_earlier_gates(self, inp: BuildStageInput, bundle: StoredBundle) -> None:
        """Nothing runs unless the AST and static gates passed this very bundle."""
        with self.sessions() as session:
            repo = GateResultRepository(session, TenantContext(inp.tenant_id, inp.project_id))
            for gate in (*AST_GATES, *STATIC_GATES):
                row = repo.for_stage(
                    inp.sub_skill_id, gate.value, inp.build_no, inp.cycle, inp.attempt
                )
                if (
                    row is None
                    or row.status != GateStatus.PASSED.value
                    or row.bundle_sha256 != bundle.bundle_sha
                ):
                    message = f"{gate.value} has not passed this bundle: it is not run"
                    raise fail(message, bundle.attempt_id, GATE_INPUT_MISMATCH)

    @activity.defn(name=STAGE)
    async def run(self, inp: BuildStageInput) -> IdResult:
        # the probe blocks (a container per module): keep it off the worker's event loop
        return await asyncio.to_thread(self.execute, inp)

    def execute(self, inp: BuildStageInput) -> IdResult:
        assembled = find_assembled(self.sessions, inp)
        bundle = read_verified(self.storage, assembled)
        self._require_earlier_gates(inp, bundle)
        image = check_image(self.image_for(inp))
        found = import_findings(self.runner.run(bundle.files, module_names(bundle.files), image))
        status = GateStatus.FAILED if found else GateStatus.PASSED
        tool_version = f"{GATE_VERSION} {image.rsplit('@', 1)[1][:30]}"[:80]
        verdict = Verdict(GateName.IMPORT_RESOLUTION, status, found, tool_version, CONFIG_SHA256)
        (recorded,) = record_verdicts(self.sessions, inp, bundle, [verdict])
        if recorded is not GateStatus.PASSED:
            activity.logger.info(
                "generated code failed the import gate",
                extra={
                    "tenant_id": str(inp.tenant_id),
                    "project_id": str(inp.project_id),
                    "sub_skill_id": str(inp.sub_skill_id),
                    "correlation_id": inp.correlation_id,
                },
            )
            raise fail("generated code failed the import gate", assembled.attempt_id, GATE_FAILED)
        return IdResult(id=assembled.attempt_id)
