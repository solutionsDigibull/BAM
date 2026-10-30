"""E12.4b against the real `sbam-toolchain` image: no network, a read-only filesystem, no root, no
capabilities, and a hostile bundle is read without being run.

Needs Docker and the image (`make up` builds `sbam-toolchain:local`, or
`docker build -t sbam-toolchain:local -f compose/docker/toolchain.Dockerfile .`). The container is
started with the same isolation settings as the `toolchain` service in compose/compose.yaml.
"""

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sbam_workers.build.gates.static_tools import run_bandit, run_mypy, run_ruff
from sbam_workers.build.gates.toolchain import LocalExecutor, SidecarExecutor, ToolFailure

DOCKER = shutil.which("docker") or "docker"
IMAGE = "sbam-toolchain:local"


def isolation(uid: int, gid: int) -> list[str]:
    """The isolation flags of the compose `toolchain` service, for the user `uid:gid`."""
    return [
        "--network", "none",
        "--read-only",
        "--user", f"{uid}:{gid}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--pids-limit", "256",
        "--tmpfs", "/tmp:size=64m,mode=1777",  # noqa: S108 - the container's own tmpfs
        "--tmpfs", f"/work:size=256m,uid={uid},gid={gid}",
    ]  # fmt: skip


# What the image runs as (and what compose sets): the isolation tests check this exact user.
ISOLATION = isolation(10001, 10001)


def same_user_as_the_test() -> tuple[int, int]:
    """In production the worker and the toolchain are both uid 10001, so they share the job
    directory. Here the test process plays the worker, so the container must run as the same user:
    on Linux (a CI runner is uid 1001) it could not read or write the directory the test created
    otherwise. Docker Desktop's bind mounts hide the difference, so only the runner caught it."""
    getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
    return (getuid(), getgid()) if getuid and getgid else (10001, 10001)


pytestmark = pytest.mark.integration


def docker(
    *args: str, check: bool = True, timeout: float = 120.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed docker CLI call
        [DOCKER, *args], capture_output=True, text=True, timeout=timeout, check=check
    )


@pytest.fixture(scope="module", autouse=True)
def image() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker is not available")
    if docker("image", "inspect", IMAGE, check=False).returncode != 0:
        pytest.skip(f"{IMAGE} is not built (docker build -f compose/docker/toolchain.Dockerfile .)")


def run_in_image(code: str) -> subprocess.CompletedProcess[str]:
    """`python -c code` inside the image, isolated exactly like the compose service."""
    return docker(
        "run", "--rm", *ISOLATION, "--entrypoint", "python", IMAGE, "-c", code, check=False
    )


def test_e12_4b_the_container_cannot_open_a_network_connection() -> None:
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    print('CONNECTED')\n"
        "except OSError as e:\n"
        "    print('BLOCKED', type(e).__name__)\n"
    )
    out = run_in_image(code).stdout
    assert "BLOCKED" in out and "CONNECTED" not in out


def test_e12_4b_the_root_filesystem_is_read_only_and_only_the_scratch_is_writable() -> None:
    code = (
        "import pathlib\n"
        "def can(path):\n"
        "    try:\n"
        "        pathlib.Path(path).write_text('x'); return True\n"
        "    except OSError:\n"
        "        return False\n"
        "print('root', can('/etc/pwned'), 'opt', can('/opt/sbam/pwned'), 'work', can('/work/ok'))\n"
    )
    assert run_in_image(code).stdout.strip() == "root False opt False work True"


def test_e12_4b_the_container_runs_as_uid_10001_with_no_capabilities() -> None:
    code = (
        "import os\n"
        "caps = [l for l in open('/proc/self/status') if l.startswith('CapEff')][0].split()[1]\n"
        "print(os.getuid(), os.getgid(), int(caps, 16))\n"
    )
    assert run_in_image(code).stdout.strip() == "10001 10001 0"


@pytest.fixture
def toolchain() -> Iterator[Path]:
    """The runner container over a fresh jobs directory; yields the directory."""
    jobs = Path(tempfile.mkdtemp(prefix="sbam-jobs-"))
    name = f"sbam-toolchain-test-{uuid.uuid4().hex[:8]}"
    uid, gid = same_user_as_the_test()
    docker(
        "run", "-d", "--name", name, *isolation(uid, gid), "-e", "TOOLCHAIN_JOBS=/jobs",
        "-v", f"{jobs}:/jobs", IMAGE,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if docker("inspect", "-f", "{{.State.Running}}", name).stdout.strip() == "true":
                break
            time.sleep(0.5)
        time.sleep(1.5)  # the runner's first pass
        running = docker("inspect", "-f", "{{.State.Running}}", name).stdout.strip()
        assert running == "true", docker("logs", name, check=False).stderr[
            -600:
        ]  # it must be serving
        yield jobs
    finally:
        docker("rm", "-f", name, check=False)
        shutil.rmtree(jobs, ignore_errors=True)


SOURCE = {"action.py": b"import os\n\n\ndef action(value):\n    unused = 1\n    return value\n"}


def test_e12_4b_the_container_gives_the_same_findings_as_the_local_tools(toolchain: Path) -> None:
    executor, local = SidecarExecutor(toolchain, timeout=90.0), LocalExecutor()
    for tool in (run_ruff, run_mypy, run_bandit):
        assert tool(SOURCE, executor) == tool(SOURCE, local), tool.__name__


def test_e12_4b_a_hostile_bundle_is_read_and_never_run(toolchain: Path) -> None:
    hostile = (
        b"import os, socket\n"
        b"os.system('touch /work/pwned')\n"
        b"open('/tmp/pwned', 'w').write('x')\n"
        b"socket.create_connection(('1.1.1.1', 53))\n"
        b"raise SystemExit('executed')\n"
    )
    result = run_bandit({"evil.py": hostile}, SidecarExecutor(toolchain, timeout=90.0))
    assert {"B605", "B108"} & {f["code"] for f in result.findings}  # it was read and judged
    assert not (toolchain / "pwned").exists()
    names = docker("ps", "-q", "--filter", "ancestor=" + IMAGE).stdout.split()
    assert names, "the runner is still serving"
    for cid in names:
        scratch_dirs = ("/work", "/tmp")  # noqa: S108 - the container's tmpfs
        listing = docker("exec", cid, "ls", *scratch_dirs, check=False).stdout
        assert "pwned" not in listing  # nothing the bundle says ran in the container either


def test_e12_4b_with_no_runner_the_worker_gets_no_verdict_rather_than_a_pass(
    tmp_path: Path,
) -> None:
    with pytest.raises(ToolFailure):
        run_ruff(SOURCE, SidecarExecutor(tmp_path, timeout=1.0, poll=0.1))
