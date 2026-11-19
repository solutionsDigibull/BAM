"""WG-29 (#867) against a real Docker daemon: the docker builder builds a Workflow-server-shaped
context (a Dockerfile with the `COPY vendor/` layer) and gives back the image id.

Needs a docker client and a reachable daemon; skipped otherwise, like the other container tests.
The Dockerfile starts `FROM scratch`, so nothing is pulled and no network is used. The image is
removed afterwards.
"""

import shutil
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sbam_workers.build.image_builder import DIGEST, DockerCliBuilder

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker")


def docker(
    *args: str, timeout: int = 60, check: bool = False
) -> "subprocess.CompletedProcess[bytes]":
    """One docker client call with a list of arguments and no shell."""
    assert DOCKER is not None
    return subprocess.run(  # noqa: S603
        [DOCKER, *args], capture_output=True, timeout=timeout, check=check
    )


def daemon_up() -> bool:
    return DOCKER is not None and docker("info", timeout=30).returncode == 0


needs_docker = pytest.mark.skipif(not daemon_up(), reason="no reachable docker daemon")


@pytest.fixture
def tag() -> Iterator[str]:
    name = f"wf_it_{uuid.uuid4().hex[:8]}:test"
    yield name
    if DOCKER is not None:
        docker("rmi", "-f", name)


@needs_docker
def test_wg29_it_the_builder_builds_the_context_and_returns_the_image_id(tag: str) -> None:
    files = {
        "Dockerfile": b"FROM scratch\nCOPY vendor/ /app/vendor/\nCOPY server.py /app/\n",
        "server.py": b"print('x')\n",
        "vendor/lib@" + "c" * 40 + "/lib.py": b"X = 1\n",
    }
    built = DockerCliBuilder(timeout_seconds=300).build(files, tag)
    assert DIGEST.match(built.digest)
    inspect = docker("image", "inspect", "--format", "{{.Id}}", tag, check=True)
    assert inspect.stdout.decode().strip() == built.digest
