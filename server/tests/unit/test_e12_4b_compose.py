"""E12.4b: the `toolchain` sidecar is in the local stack and in no deployed environment.

Static, like test_e16_3_deploy.py: `docker compose config` resolves the files and starts nothing.
The image is built on the host and is not in the release digests yet, so a deployed environment
(dev VM, staging, prod) must run the static tools in the worker (an empty `TOOLCHAIN_JOBS_DIR`):
a worker that points at a runner which is not there would end every build in BUILD_ERROR.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "compose"
DOCKER = shutil.which("docker")


def load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lint = load("scripts/compose_lint.py", "compose_lint_e12_4b")


def _compose_ok() -> bool:
    if DOCKER is None:
        return False
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [DOCKER, "compose", "version"], capture_output=True, text=True, check=False
    )
    return proc.returncode == 0


needs_docker = pytest.mark.skipif(not _compose_ok(), reason="docker compose not installed")


@pytest.fixture(scope="module")
def env_file() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        yield lint.synth_env_file(Path(tmp))


def local_config(env_file: Path) -> dict[str, Any]:
    """compose.yaml with its override, the way `make up` runs a laptop."""
    cmd = [str(DOCKER), "compose", "--env-file", str(env_file)]
    for name in ("compose.yaml", "compose.override.yaml"):
        cmd += ["-f", str(COMPOSE / name)]
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [*cmd, "config", "--format", "json"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    config: dict[str, Any] = json.loads(proc.stdout)
    return config


@needs_docker
def test_e12_4b_the_local_stack_runs_the_toolchain_isolated_and_worker_build_uses_it(
    env_file: Path,
) -> None:
    services = local_config(env_file)["services"]
    toolchain = services["toolchain"]
    assert toolchain["network_mode"] == "none"  # no network at all
    assert toolchain["read_only"] is True and toolchain["user"] == "10001:10001"
    assert (
        toolchain["cap_drop"] == ["ALL"] and "no-new-privileges:true" in toolchain["security_opt"]
    )
    assert toolchain["cpus"] and toolchain["mem_limit"] and toolchain["pids_limit"]
    assert toolchain["healthcheck"]["test"]  # worker-build waits for it to be healthy
    worker = services["worker-build"]
    assert worker["environment"]["TOOLCHAIN_JOBS_DIR"] == "/jobs"
    assert "toolchain" in worker["depends_on"]
    assert not toolchain.get("ports") and "docker.sock" not in json.dumps(services)


@needs_docker
@pytest.mark.parametrize("env", lint.ENVS)
def test_e12_4b_a_deployed_environment_has_no_toolchain_and_the_worker_does_not_wait_for_it(
    env: str, env_file: Path
) -> None:
    services = lint.resolve(env, [], env_file)["services"]
    assert "toolchain" not in services and "toolchain-init" not in services
    worker = services["worker-build"]
    assert "TOOLCHAIN_JOBS_DIR" not in worker.get("environment", {})
    assert "toolchain" not in worker.get("depends_on", {})
    mounts = [v.get("source", "") for v in worker.get("volumes", [])]
    assert not any("toolchain-jobs" in str(m) for m in mounts)


@needs_docker
@pytest.mark.parametrize("env", lint.ENVS)
def test_e12_4b_the_compose_lint_finds_nothing_about_the_toolchain(
    env: str, env_file: Path
) -> None:
    findings = lint.lint_config(lint.resolve(env, [], env_file), env)
    assert [str(f) for f in findings if "toolchain" in str(f)] == []
