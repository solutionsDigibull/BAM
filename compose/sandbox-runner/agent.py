"""The local-docker sandbox agent (E13.2, ADR 0023 decision 6). Standard library only.

Runs inside the `sandbox-runner` container (internal network, read-only root, no capabilities,
non-root) and gives the worker the same five verbs a CubeSandbox VM gives: create, put files, exec,
get file, destroy. A "VM" here is a private directory plus the processes started in it. That is
**weaker isolation than a microVM**: runs share one container and one kernel, which is why the
worker stamps every result `LOCAL_DOCKER_NONCERT` and certification refuses it.

Rules worth knowing
  * Every request needs the bearer token (constant-time compare). No token: it refuses to start.
  * A VM path is absolute (`/work/request.json`), is mapped under the VM's own directory, and is
    refused if it has `..`, a NUL, or its real location (symlinks resolved) is outside the VM.
    Files are written with `O_NOFOLLOW|O_EXCL`; the last component can never be a link.
  * `exec` takes an argument vector, never a shell string. Arguments that start with `/work/` are
    rewritten to the VM directory (there is no chroot); the runner writes `out/` next to the
    request file it is given.
  * Each command runs in its own session with resource limits. A timeout or an output over the cap
    kills the whole process group. Destroy kills every process of the VM and removes its directory.
  * One run at a time (`SANDBOX_AGENT_MAX_VMS`, default 1). Every VM directory belongs to the same
    uid, so two live runs could read each other's files; a second `create` is refused (429) and the
    worker retries it as infrastructure.
  * The agent is not dumpable, so test code (same uid) cannot read its token from
    `/proc/<pid>/environ` or its memory. It is also a child subreaper: a daemon that escapes its
    session is reparented to the agent, and `destroy` kills every descendant, so nothing outlives a
    run into the next one.
  * Hosts-file writes are accepted and ignored: Docker's internal DNS already limits what resolves.
"""

import base64
import binascii
import ctypes
import hmac
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_VMS = int(os.environ.get("SANDBOX_AGENT_MAX_VMS", "1"))  # see "one run at a time"
NPROC = int(
    os.environ.get("SANDBOX_AGENT_NPROC", "64")
)  # per uid; 0 = rely on pids_limit
MAX_BODY = 16 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_FILES = 200
MAX_OUTPUT_CAP = 10 * 1024 * 1024
MAX_TIMEOUT_S = 1800
PATH_OK = re.compile(r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+")
VM_ID = re.compile(r"[0-9a-f]{32}")
SAFE_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
HOSTS = "/etc/hosts"


class Refused(Exception):  # noqa: N818
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Vm:
    def __init__(self, root: Path, ttl: int, run_id: str) -> None:
        self.id = uuid.uuid4().hex
        self.dir = root / self.id
        self.dir.mkdir(mode=0o700)
        self.expires = time.monotonic() + ttl
        self.run_id = run_id
        self.pids: set[int] = set()
        self.lock = threading.Lock()


class Agent:
    def __init__(self, root: Path, token: str, runtime_digest: str = "") -> None:
        self.runtime_digest = (
            runtime_digest  # what this container's image is (set by compose)
        )
        if len(token) < 16:
            raise SystemExit("SANDBOX_AGENT_TOKEN must be at least 16 characters")
        self.root = root
        self.token = token.encode()
        self.vms: dict[str, Vm] = {}
        self.lock = threading.Lock()

    def vm(self, vm_id: str) -> Vm:
        found = self.vms.get(vm_id) if VM_ID.fullmatch(vm_id) else None
        if found is None:
            raise Refused(404, "no such vm")
        return found

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        ttl = int(body.get("ttl_seconds", 600))
        if not 10 <= ttl <= 7200:
            raise Refused(422, "ttl out of range")
        with self.lock:
            self.reap()
            if len(self.vms) >= MAX_VMS:
                raise Refused(429, "too many vms")
            vm = Vm(self.root, ttl, str(body.get("run_id", ""))[:64])
            self.vms[vm.id] = vm
        return {"vm_id": vm.id}

    def reap(self) -> None:
        now = time.monotonic()
        for vm_id in [i for i, v in self.vms.items() if v.expires < now]:
            self.destroy(vm_id)

    def resolve(self, vm: Vm, path: str) -> Path:
        if (
            not isinstance(path, str)
            or not PATH_OK.fullmatch(path)
            or ".." in path.split("/")
        ):
            raise Refused(422, "bad path")
        target = vm.dir / path.lstrip("/")
        # test code can plant a symlink in its own directory: judge the real location, not the text
        home = os.path.realpath(vm.dir)
        if os.path.commonpath([home, os.path.realpath(target)]) != home:
            raise Refused(422, "bad path")
        return target

    def put_files(self, vm: Vm, body: dict[str, Any]) -> None:
        files = body.get("files")
        if not isinstance(files, dict) or len(files) > MAX_FILES:
            raise Refused(422, "bad files")
        decoded: dict[str, bytes] = {}
        for path, text in files.items():
            try:
                data = base64.b64decode(text, validate=True)
            except (binascii.Error, TypeError, ValueError):
                raise Refused(422, "bad file encoding") from None
            if len(data) > MAX_FILE:
                raise Refused(413, "file too large")
            decoded[path] = data
        for path, data in decoded.items():
            if path == HOSTS:
                continue  # accepted and ignored, see the module docstring
            target = self.resolve(vm, path)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            try:
                fd = os.open(target, flags, 0o600)
            except FileExistsError:
                raise Refused(409, "file exists") from None
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)

    def get_file(self, vm: Vm, path: str, max_bytes: int) -> bytes | None:
        target = self.resolve(vm, path)
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError:
            raise Refused(422, "not a readable file") from None
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise Refused(413, "file too large")
        return data

    def run(self, vm: Vm, body: dict[str, Any]) -> dict[str, Any]:
        argv = body.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(a, str) for a in argv)
        ):
            raise Refused(422, "argv must be a list of strings")
        timeout = float(body.get("timeout_s", 0))
        cap = int(body.get("max_output_bytes", 0))
        if not 0 < timeout <= MAX_TIMEOUT_S or not 0 < cap <= MAX_OUTPUT_CAP:
            raise Refused(422, "timeout or output cap out of range")
        env = {
            # the image's own PATH: the runtime's virtualenv comes first, never a system interpreter
            "PATH": os.environ["PATH"],
            "HOME": str(vm.dir),
            "PYTHONHASHSEED": "0",
        }
        for key, value in (body.get("env") or {}).items():
            if (
                not SAFE_ENV.fullmatch(key)
                or not isinstance(value, str)
                or "\0" in value
            ):
                raise Refused(422, "bad environment")
            env[key] = value
        # what a microVM's /etc/sbam-runtime says; set last, so a request cannot change it
        env["SANDBOX_RUNTIME_DIGEST"] = self.runtime_digest
        mapped = [self._map_arg(vm, a) for a in argv]
        return self._spawn(vm, mapped, env, timeout, cap)

    def _map_arg(self, vm: Vm, arg: str) -> str:
        if "\0" in arg:
            raise Refused(422, "bad argument")
        if arg.startswith("/work/"):
            return str(self.resolve(vm, arg))
        return arg

    def _spawn(
        self, vm: Vm, argv: list[str], env: dict[str, str], timeout: float, cap: int
    ) -> dict[str, Any]:
        def limits() -> None:
            os.setsid()
            if NPROC:
                resource.setrlimit(resource.RLIMIT_NPROC, (NPROC, NPROC))
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE * 2, MAX_FILE * 2))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        out_path, err_path = vm.dir / ".stdout", vm.dir / ".stderr"
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            try:
                proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    argv,
                    cwd=vm.dir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    preexec_fn=limits,
                    close_fds=True,
                )
            except OSError:
                return {
                    "exit_code": 127,
                    "stdout": "",
                    "stderr": "",
                    "truncated": False,
                    "timed_out": False,
                }
            with vm.lock:
                vm.pids.add(proc.pid)
            timed_out = truncated = False
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    timed_out = True
                    break
                if out_path.stat().st_size > cap or err_path.stat().st_size > cap:
                    truncated = True
                    break
                time.sleep(0.05)
            # always sweep the group, not only on a timeout: a command that exits 0 after starting
            # a background process must not leave it running (it would outlive `destroy`)
            self._kill(proc.pid)
            code = proc.wait()
        with vm.lock:
            vm.pids.discard(proc.pid)
        stdout = out_path.read_bytes()[:cap]
        stderr = err_path.read_bytes()[:cap]
        truncated = (
            truncated or out_path.stat().st_size > cap or err_path.stat().st_size > cap
        )
        out_path.unlink(missing_ok=True)
        err_path.unlink(missing_ok=True)
        return {
            "exit_code": code,
            "stdout": base64.b64encode(stdout).decode(),
            "stderr": base64.b64encode(stderr).decode(),
            "truncated": truncated,
            "timed_out": timed_out,
        }

    @staticmethod
    def _kill(pid: int) -> None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def destroy(self, vm_id: str) -> None:
        vm = self.vms.pop(vm_id, None)
        if vm is None:
            return  # idempotent
        with vm.lock:
            pids = list(vm.pids)
        for pid in pids:
            self._kill(pid)
        sweep_descendants()
        shutil.rmtree(vm.dir, ignore_errors=True)

    def listing(self) -> list[dict[str, Any]]:
        return [{"vm_id": v.id, "run_id": v.run_id} for v in self.vms.values()]


def descendants(root_pid: int) -> list[int]:
    """Every process below `root_pid` (children, grandchildren, reparented orphans)."""
    children: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                stat = handle.read().decode("utf-8", "replace")
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue  # the process ended while we looked
        children.setdefault(ppid, []).append(int(entry))
    found: list[int] = []
    queue = [root_pid]
    while queue:
        for child in children.get(queue.pop(), []):
            found.append(child)
            queue.append(child)
    return found


def sweep_descendants() -> None:
    """Kill and reap everything the agent started and everything it inherited from them."""
    for pid in descendants(os.getpid()):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue
    for pid in descendants(os.getpid()):
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass  # not ours to reap (another thread's Popen reaps its own)


def harden_process() -> None:
    """Called once at start: no /proc peeking by same-uid test code, and orphans come to us."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE = 4
        raise SystemExit("cannot mark the agent non-dumpable")
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER = 36
        raise SystemExit("cannot become a child subreaper")


def handler_for(agent: Agent) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "sbam-sandbox-agent"

        def log_message(self, format: str, *args: Any) -> None:
            return  # request lines carry paths; the worker keeps the evidence

        def _reply(self, status: int, body: Any) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise Refused(413, "body too large")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise Refused(422, "body must be an object")
            return data

        def _handle(self, method: str) -> None:
            try:
                sent = (
                    self.headers.get("Authorization", "")
                    .removeprefix("Bearer ")
                    .encode()
                )
                if not hmac.compare_digest(sent, agent.token):
                    raise Refused(401, "unauthorized")
                parts = self.path.split("?")[0].strip("/").split("/")
                if method == "GET" and parts == ["v1", "runtime"]:
                    self._reply(200, {"digest": agent.runtime_digest})
                    return
                if parts[:2] != ["v1", "vms"]:
                    raise Refused(404, "not found")
                self._reply(200, self._route(method, parts[2:]))
            except Refused as exc:
                self._reply(exc.status, {"error": str(exc)})
            except (ValueError, TypeError, json.JSONDecodeError):
                self._reply(422, {"error": "bad request"})

        def _route(self, method: str, rest: list[str]) -> Any:
            if method == "GET" and not rest:
                return {"vms": agent.listing()}
            if method == "POST" and not rest:
                return agent.create(self._body())
            if not rest:
                raise Refused(405, "method not allowed")
            if method == "DELETE" and len(rest) == 1:
                agent.destroy(rest[0])
                return {}
            vm = agent.vm(rest[0])
            action = rest[1] if len(rest) == 2 else ""
            if method == "PUT" and action == "files":
                agent.put_files(vm, self._body())
                return {}
            if method == "POST" and action == "exec":
                return agent.run(vm, self._body())
            if method == "POST" and action == "file":
                body = self._body()
                data = agent.get_file(
                    vm, body.get("path", ""), int(body.get("max_bytes", 0))
                )
                return {
                    "found": data is not None,
                    "data": base64.b64encode(data or b"").decode(),
                }
            raise Refused(404, "not found")

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

    return Handler


def main() -> None:
    harden_process()
    token = os.environ.pop(
        "SANDBOX_AGENT_TOKEN", ""
    )  # not inherited by the commands it runs
    root = Path(os.environ.get("SANDBOX_AGENT_ROOT", "/tmp/sbam-vms"))  # noqa: S108
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    agent = Agent(root, token, os.environ.get("SANDBOX_RUNTIME_DIGEST", ""))
    server = ThreadingHTTPServer(
        (
            os.environ.get("SANDBOX_AGENT_HOST", "0.0.0.0"),  # noqa: S104
            int(os.environ.get("SANDBOX_AGENT_PORT", "8091")),
        ),
        handler_for(agent),
    )
    signal.signal(
        signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start()
    )
    print(
        "sandbox-agent listening, mode=LOCAL_DOCKER_NONCERT",
        file=sys.stderr,
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
