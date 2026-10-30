"""Where the static tools run (E12.4, E12.4b): a child process, or the `sbam-toolchain` sidecar.

An executor takes a tool name (`ruff`, `mypy` or `bandit`), the bundle's Python files and the
tool's pinned config, and gives back what the tool printed and the version that printed it. Neither
runs anything the bundle contains: the tools only read source.

* `LocalExecutor` starts the tool as a separate Python process on a scratch copy (an empty
  environment, a timeout, no shell). It is what a laptop and the tests use.
* `SidecarExecutor` hands the same work to the `sbam-toolchain` container through a shared
  directory: a job directory with the files, the config and a `request.json`, `READY` written last,
  and a `result.json` then `DONE` back. That container has no network, a read-only filesystem and
  no root (compose/docker/toolchain/runner.py), so a tool that misbehaves on hostile source has
  nothing to reach. The version recorded is the sidecar's own.

A tool that cannot give a verdict (a timeout, a crash, no sidecar answering, a refused job) raises
`ToolFailure`: retried by Temporal and never a pass, never a correction.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Final, Protocol

PACKAGE: Final = "skill"
OK_CODES: Final = (0, 1)  # the tools exit 1 when they found something
CONFIG_NAME: Final = {"ruff": "ruff.toml", "mypy": "mypy.ini"}
COMMANDS: Final[Mapping[str, tuple[str, ...]]] = {
    "ruff": (
        "-m",
        "ruff",
        "check",
        "--config",
        "ruff.toml",
        "--no-cache",
        "--output-format",
        "json",
        PACKAGE,
    ),
    "mypy": (
        "-m",
        "mypy",
        "--config-file",
        "mypy.ini",
        "--output",
        "json",
        "--no-site-packages",
        "--cache-dir",
        ".mypy_cache",
        PACKAGE,
    ),
    "bandit": ("-m", "bandit", "-r", PACKAGE, "-f", "json", "-q"),
}


class ToolFailure(Exception):  # noqa: N818 - the tool gave no verdict; retryable
    """A tool could not give a verdict (never a finding on the code)."""


@dataclass(frozen=True)
class ToolOutput:
    stdout: str
    version: str


class Executor(Protocol):
    def run(self, tool: str, files: Mapping[str, bytes], config: str | None) -> ToolOutput: ...


def _check(tool: str, returncode: int | None) -> None:
    if returncode not in OK_CODES:
        raise ToolFailure(f"{tool} exited with {returncode}")


class LocalExecutor:
    def __init__(self, timeout: float = 240.0) -> None:
        self.timeout = timeout

    def run(self, tool: str, files: Mapping[str, bytes], config: str | None) -> ToolOutput:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if config is not None:
                (root / CONFIG_NAME[tool]).write_text(config)
            package = root / PACKAGE
            for name, data in files.items():
                target = package / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (package / "__init__.py").touch(exist_ok=True)
            try:
                done = subprocess.run(  # noqa: S603 - fixed argv, never a shell
                    [sys.executable, *COMMANDS[tool]],
                    cwd=root,
                    env={},
                    capture_output=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise ToolFailure(f"{tool} timed out") from None
            except OSError as exc:
                raise ToolFailure(f"{tool} could not start: {type(exc).__name__}") from None
        _check(tool, done.returncode)
        return ToolOutput(done.stdout.decode("utf-8", "replace"), version(tool))


class SidecarExecutor:
    """Jobs for the `sbam-toolchain` container, through the shared `jobs_dir`."""

    def __init__(
        self,
        jobs_dir: Path,
        timeout: float = 300.0,
        poll: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.jobs_dir, self.timeout, self.poll = jobs_dir, timeout, poll
        self.clock, self.sleep = clock, sleep

    def _write(self, job: Path, tool: str, files: Mapping[str, bytes], config: str | None) -> None:
        for name, data in files.items():
            target = job / "files" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if config is not None:
            (job / "config").write_text(config)
        (job / "request.json").write_text(json.dumps({"tool": tool}))
        (job / "READY").touch()  # last: the runner takes a job only once it is complete

    def run(self, tool: str, files: Mapping[str, bytes], config: str | None) -> ToolOutput:
        if (
            not self.jobs_dir.is_dir()
        ):  # not mounted: never invent it and wait for a runner that cannot see it
            raise ToolFailure(f"{tool}: the toolchain jobs directory is not mounted")
        job = self.jobs_dir / uuid.uuid4().hex
        try:
            job.mkdir()
            self._write(job, tool, files, config)
            deadline = self.clock() + self.timeout
            while not (job / "DONE").exists():
                if self.clock() > deadline:
                    raise ToolFailure(f"{tool}: the toolchain did not answer in time")
                self.sleep(self.poll)
            result = json.loads((job / "result.json").read_text(encoding="utf-8"))
        except OSError as exc:
            raise ToolFailure(
                f"{tool}: the toolchain jobs directory failed: {type(exc).__name__}"
            ) from None
        except ValueError:
            raise ToolFailure(f"{tool}: the toolchain answered with something unreadable") from None
        finally:
            shutil.rmtree(job, ignore_errors=True)
        if result.get("error"):
            raise ToolFailure(f"{tool}: the toolchain says {str(result['error'])[:80]}")
        _check(tool, result.get("returncode"))
        return ToolOutput(str(result.get("stdout", "")), str(result.get("version", "")))
