"""Parse an untrusted file in a resource-limited child process (A-21b, #492).

A parser is written for well-formed input, and a crafted PDF can make it burn CPU, allocate until
the 1 GiB container is killed, or crash a C library: all of it inside the worker that serves every
tenant. `run_isolated` runs the real loader in a fresh interpreter instead:

* the environment is scrubbed (no database URL, no API key reaches a process that reads hostile
  bytes) and the working directory is a private temporary directory;
* the child sets `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_CORE` and an `RLIMIT_AS` of its loaded size
  plus `MEMORY_BYTES` on itself before it reads a byte (no `preexec_fn`: the worker is
  multi-threaded; the imports alone reserve about 900 MB of address space, so a fixed cap would
  refuse every file);
* the parent kills the whole process group at the wall-clock deadline, and beats the activity
  heartbeat while it waits (the child cannot);
* the child writes its result as JSON to a file inside that directory (so `RLIMIT_FSIZE` bounds it);
  a timeout is `TIMEOUT`, a signal, a limit or an unreadable result is `PARSE_ERROR`.

A tenant holds at most one slot at a time, inside a global bound, so one tenant's stream of hostile
files cannot occupy every slot. Only loaders that return their result eagerly (a PDF) can be
isolated; a lazy one (a big workbook) streams from the worker's own memory by design.
"""

import contextlib
import contextvars
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, ParseReport, Table

from sbam_workers.ingest.loaders.base import Heartbeat, Parsed, ParseError

MEMORY_BYTES = 384 * 1024 * 1024  # address space the file may add to the loaded interpreter
CPU_SECONDS = 30
WALL_SECONDS = 45
RESULT_MAX_BYTES = 128 * 1024 * 1024  # the child's result file (parsed text is capped at 120 MiB)
GLOBAL_SLOTS = 1  # parent + loaded child + MEMORY_BYTES must fit the 1 GiB ingest container
BEAT_SECONDS = 5.0
KILL_GRACE_SECONDS = 5.0

# Loader name -> (module, function): the child imports only the one it runs.
ISOLATED_LOADERS = {"pdf": ("sbam_workers.ingest.loaders.pdf", "load_pdf")}

# The tenant whose file this thread is parsing (set by the parse step; None = no per-tenant limit).
current_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sbam_parse_tenant", default=None
)

_global_slots = threading.BoundedSemaphore(GLOBAL_SLOTS)
_tenants_busy: set[str] = set()
_tenants_changed = threading.Condition()


@contextlib.contextmanager
def parse_slot(tenant: str | None, beat: Heartbeat) -> Iterator[None]:
    """Wait for this tenant's slot (one at a time), then for a global one."""
    if tenant is not None:
        with _tenants_changed:
            while tenant in _tenants_busy:
                _tenants_changed.wait(timeout=BEAT_SECONDS)
                beat()
            _tenants_busy.add(tenant)
    try:
        while not _global_slots.acquire(timeout=BEAT_SECONDS):
            beat()
        try:
            yield
        finally:
            _global_slots.release()
    finally:
        if tenant is not None:
            with _tenants_changed:
                _tenants_busy.discard(tenant)
                _tenants_changed.notify_all()


def _child_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=KILL_GRACE_SECONDS)


def run_isolated(
    name: str, data: bytes, beat: Heartbeat, *, wall_seconds: float = WALL_SECONDS
) -> Parsed:
    """Run loader `name` on `data` in a child process and return what it returned."""
    if name not in ISOLATED_LOADERS:
        raise ValueError(f"loader {name!r} cannot be isolated")
    with (
        parse_slot(current_tenant.get(), beat),
        tempfile.TemporaryDirectory(prefix="sbam-parse-") as tmp,
    ):
        result_path = Path(tmp) / "result.json"
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            [
                sys.executable,
                "-I",
                "-m",
                "sbam_workers.ingest.isolated_child",
                name,
                str(result_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_child_env(),
            cwd=tmp,
            start_new_session=True,  # its own process group: one kill reaches any grandchild
        )
        try:
            _wait(proc, data, beat, wall_seconds)
        finally:
            _kill_group(proc)  # no-op when it exited; reaps stragglers either way
        return _read_result(proc.returncode, result_path)


def _wait(proc: subprocess.Popen[bytes], data: bytes, beat: Heartbeat, wall_seconds: float) -> None:
    deadline = time.monotonic() + wall_seconds
    feeder = threading.Thread(target=_feed, args=(proc, data), daemon=True)
    feeder.start()
    while proc.poll() is None:
        if time.monotonic() >= deadline:
            _kill_group(proc)
            raise ParseError(ErrorCode.TIMEOUT, "the file took too long to read")
        beat()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=min(BEAT_SECONDS, max(0.05, deadline - time.monotonic())))
    feeder.join(timeout=1)


def _feed(proc: subprocess.Popen[bytes], data: bytes) -> None:
    """Write the file to the child's stdin; a child that died first is not an error here."""
    with contextlib.suppress(BrokenPipeError, OSError, ValueError):
        assert proc.stdin is not None  # noqa: S101 - stdin=PIPE
        proc.stdin.write(data)
        proc.stdin.close()


def _read_result(returncode: int | None, path: Path) -> Parsed:
    try:
        raw = path.read_bytes() if path.stat().st_size <= RESULT_MAX_BYTES else b""
        payload: dict[str, Any] = json.loads(raw)
    except (OSError, ValueError):
        payload = {}
    if "error" in payload:
        error = payload["error"]
        try:
            code = ErrorCode(error["code"])
        except (KeyError, ValueError):
            code = ErrorCode.PARSE_ERROR
        raise ParseError(code, str(error.get("message", "the file could not be read"))[:200])
    if "parsed" not in payload:
        if returncode == -signal.SIGXCPU:  # RLIMIT_CPU
            raise ParseError(ErrorCode.TIMEOUT, "the file took too long to read")
        how = f"signal {-returncode}" if returncode and returncode < 0 else f"exit {returncode}"
        raise ParseError(ErrorCode.PARSE_ERROR, f"the reader was stopped ({how}): too big to read")
    try:
        body = payload["parsed"]
        return Parsed(
            blocks=[Block.model_validate(b) for b in body["blocks"]],
            tables=[Table.model_validate(t) for t in body["tables"]],
            markdown=str(body["markdown"]),
            report=ParseReport.model_validate(body["report"]),
        )
    except (KeyError, ValueError, TypeError):
        raise ParseError(ErrorCode.PARSE_ERROR, "the reader returned an unusable result") from None
