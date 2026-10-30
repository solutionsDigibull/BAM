"""sbam-toolchain job runner (E12.4b, #522): Ruff, MyPy and Bandit on a bundle, with no network.

The container has no network, a read-only root, a tmpfs scratch directory and no capabilities. The
build worker hands it work through a shared directory (`/jobs`) and nothing else:

    /jobs/<id>/request.json     {"tool": "ruff" | "mypy" | "bandit"}
    /jobs/<id>/files/<name>     the bundle's Python files
    /jobs/<id>/config           ruff.toml or mypy.ini (the worker's pinned, hashed config)
    /jobs/<id>/READY            written last: the job may be taken
    /jobs/<id>/result.json      {"returncode", "stdout", "error", "version"}, then DONE

Only a fixed command table runs: the request names a tool, never a command line. The files are
copied to a fresh scratch directory under `skill/` and read, never imported or executed (the three
tools parse source). Names that are absolute, hold `..` or odd characters are refused. Each run has
an empty environment and a timeout. Standard library only, so it needs nothing the tools do not.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

JOBS = Path(os.environ.get("TOOLCHAIN_JOBS", "/jobs"))
SCRATCH = Path(os.environ.get("TOOLCHAIN_SCRATCH", "/work"))
TIMEOUT = float(os.environ.get("TOOLCHAIN_TIMEOUT", "240"))
POLL = float(os.environ.get("TOOLCHAIN_POLL", "0.25"))
HEARTBEAT_DEFAULT = "/tmp/heartbeat"  # noqa: S108 - the container's own tmpfs
HEARTBEAT = Path(os.environ.get("TOOLCHAIN_HEARTBEAT", HEARTBEAT_DEFAULT))
PACKAGE = "skill"
MAX_FILES = 500
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_STDOUT = 5 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}$")

# The same argv the worker's local executor uses; `skill` is the package directory.
COMMANDS: dict[str, list[str]] = {
    "ruff": [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--config",
        "ruff.toml",
        "--no-cache",
        "--output-format",
        "json",
        PACKAGE,
    ],
    "mypy": [
        sys.executable,
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
    ],
    "bandit": [sys.executable, "-m", "bandit", "-r", PACKAGE, "-f", "json", "-q"],
}
CONFIG_NAME = {"ruff": "ruff.toml", "mypy": "mypy.ini"}


class RefusedError(ValueError):
    """The job is not one this runner will do."""


def safe_name(name: str) -> str:
    parts = name.split("/")
    if not _NAME.match(name) or name.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise RefusedError("a file name is not allowed")
    return name


def run_job(job: Path, scratch: Path = SCRATCH, timeout: float = TIMEOUT) -> dict[str, object]:
    """Run the job's tool and answer `{returncode, stdout, error}`; never raises for a bad job."""
    try:
        request = json.loads((job / "request.json").read_text(encoding="utf-8"))
        tool = request["tool"]
        if tool not in COMMANDS:
            raise RefusedError("unknown tool")
        names = sorted(
            str(p.relative_to(job / "files")).replace(os.sep, "/")
            for p in (job / "files").rglob("*")
            if p.is_file() and not p.is_symlink()
        )
        if not names or len(names) > MAX_FILES:
            raise RefusedError("no files, or too many")
        for name in names:
            safe_name(name)
        if sum((job / "files" / n).stat().st_size for n in names) > MAX_TOTAL_BYTES:
            raise RefusedError("the files are too large")
    except RefusedError as exc:
        return {"returncode": None, "stdout": "", "error": f"refused: {exc}"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"returncode": None, "stdout": "", "error": "refused: unreadable job"}
    work = Path(tempfile.mkdtemp(dir=scratch))
    try:
        for name in names:
            target = work / PACKAGE / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(job / "files" / name, target)
        (work / PACKAGE / "__init__.py").touch(exist_ok=True)
        if tool in CONFIG_NAME:
            shutil.copyfile(job / "config", work / CONFIG_NAME[tool])
        done = (
            subprocess.run(  # noqa: S603 - a fixed argv from COMMANDS, no shell, empty environment
                COMMANDS[tool], cwd=work, env={}, capture_output=True, timeout=timeout, check=False
            )
        )
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "error": "timeout"}
    except OSError as exc:
        return {"returncode": None, "stdout": "", "error": f"could not run: {type(exc).__name__}"}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out = done.stdout[:MAX_STDOUT].decode("utf-8", "replace")
    return {"returncode": done.returncode, "stdout": out, "error": None, "version": version(tool)}


def finish(job: Path, result: dict[str, object]) -> None:
    tmp = job / "result.json.tmp"
    tmp.write_text(json.dumps(result), encoding="utf-8")
    tmp.replace(job / "result.json")  # the result is whole before DONE exists
    (job / "DONE").touch()


def pending(jobs: Path) -> list[Path]:
    return sorted(
        j
        for j in jobs.iterdir()
        if j.is_dir() and (j / "READY").exists() and not (j / "DONE").exists()
    )


def serve(jobs: Path = JOBS, once: bool = False) -> None:
    while True:
        HEARTBEAT.touch()  # the container health check reads its age
        try:
            for job in pending(jobs):
                finish(job, run_job(job))
        except (
            OSError
        ):  # a job directory removed mid-scan (the worker gave up): the next pass is fine
            pass
        if once:
            return
        time.sleep(POLL)


if __name__ == "__main__":
    serve()
