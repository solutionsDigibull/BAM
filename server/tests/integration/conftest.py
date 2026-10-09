"""Integration tests run against the Compose stack on its published 127.0.0.1 ports (TD §19.3).

`make test-int` brings the stack up and runs `pytest -m integration`. Host ports come from the same
env vars as compose/.env (`API_PORT`, ...). If the stack isn't reachable the tests skip, so they
never need Docker by accident.
"""

import os
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
# Relative to the repo root, like the Makefile's ENV_FILE (pytest itself runs from server/).
ENV_FILE = str(REPO / os.environ.get("ENV_FILE", "compose/.env"))

DOCKER_COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    ENV_FILE,
    "-f",
    str(REPO / "compose/compose.yaml"),
]

Compose = Callable[..., subprocess.CompletedProcess[str]]


def port(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def is_open(p: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", p)) == 0


@pytest.fixture(scope="session", autouse=True)
def _stack_up() -> None:
    if not is_open(port("API_PORT", 8000)):
        # CI's compose-smoke must never pass by skipping: there the stack is required.
        if os.environ.get("CI"):
            pytest.fail("Compose stack is not running in CI (make up failed?)")
        pytest.skip("Compose stack is not running (make up)")


@pytest.fixture(scope="session")
def compose() -> Compose:
    """Run `docker compose <args>` against the stack and return the result (never raises)."""

    def run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        cmd = [*DOCKER_COMPOSE, *args]
        return subprocess.run(  # noqa: S603 - fixed docker argv, no shell
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )

    return run
