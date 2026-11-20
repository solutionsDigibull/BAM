"""A-02 (#428), ADR 0026 decision 3: what the static checks miss, the sandbox contains.

Each test runs a bundle whose action tool does one thing a prompt-injected model could write (files
are put into the bundle after assembly, so the pre-check and AST gate never see them: the sandbox is
the only thing between the code and the host) and reads back what the platform concluded.

Two backends:

* `cube`: the CubeSandbox microVM, the boundary that certifies. It needs a KVM host (#122), and runs
  only when `SANDBOX_MODE=CUBESANDBOX` is set (the settings of `docs/runbooks/sandbox-host.md`, plus
  `SBAM_CUBE_RUNTIME_DIGEST`); without it the tests skip. Set, but incomplete, they fail: a missing
  setting is never a pass.
* `local`: the hardened local container (needs Docker and `skill-runtime:local`). Not a boundary
  and never certifies; it shows the payloads do what they claim and that the container holds them.

Missing prerequisite on a laptop: a CubeSandbox host with a READY template (#122).
"""

import asyncio
import os
import re
import uuid
from dataclasses import replace
from typing import Any

import pytest
from sbam_contracts.sandbox import EgressVerdict, FailureCategory, SandboxLimits, SandboxMode
from sbam_workers.sandbox.executor import ExecutionResult, RunInput, execute
from sbam_workers.sandbox.factory import make_sandbox, problem
from sbam_workers.settings import WorkerSettings

from .test_e13_2_forgery import attack, one_case, reported
from .test_e13_2_runner_container import (  # noqa: F401 - agent_url is a fixture
    agent_url,
    run,
    run_input,
)

pytestmark = pytest.mark.integration

SHORT = SandboxLimits(case_timeout_s=10)

READ_SYSTEM_FILE = """\
results.append("passwd-read-%d" % len(open("/etc/passwd").read().splitlines()))
for target in ("/etc/passwd", "/opt/sbam/harness/run_cases.py"):
    try:
        open(target, "ab").close()
        results.append("writable:" + target)
    except OSError:
        results.append("read-only:" + target)
"""
OPEN_SOCKET = """\
import socket
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    results.append("socket-connected")
except OSError:
    results.append("socket-blocked")
"""
FORK_BOMB = """\
children = 0
try:
    for _ in range(2000):
        if os.fork() == 0:
            import time
            time.sleep(300)
            os._exit(0)
        children += 1
except OSError:
    pass
results.append("forked-%d" % children)
"""
ALLOCATE_10GB = """\
blob = bytearray(10 * 1024 * 1024 * 1024)
results.append("allocated")
"""
SLEEP_PAST_DEADLINE = """\
import time
time.sleep(120)
results.append("slept")
"""


def cube_runner(spec: RunInput) -> ExecutionResult:
    if os.environ.get("SANDBOX_MODE") != SandboxMode.CUBESANDBOX.value:
        pytest.skip("SANDBOX_MODE=CUBESANDBOX is not set: needs a CubeSandbox KVM host (#122)")
    settings = WorkerSettings.model_validate(  # the two fields the sandbox does not use
        {"temporal_host": "localhost:7233", "database_url": "postgresql+psycopg://x@localhost/x"}
    )
    why = problem(settings)
    digest = os.environ.get("SBAM_CUBE_RUNTIME_DIGEST", "")
    if why or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        pytest.fail(
            f"CubeSandbox is selected but not configured: {why or 'SBAM_CUBE_RUNTIME_DIGEST'}"
        )
    sandbox = make_sandbox(settings)
    spec = replace(spec, mode=SandboxMode.CUBESANDBOX, runtime_digest=digest, run_id=uuid.uuid4())
    result: ExecutionResult = asyncio.run(
        execute(spec, sandbox.backend, egress_reader=sandbox.egress_reader)
    )
    return result


@pytest.fixture(params=["cube", "local"])
def backend(request: pytest.FixtureRequest) -> Any:
    """`run(spec) -> ExecutionResult` on the backend under test."""
    if request.param == "cube":
        return request.param, cube_runner
    url = request.getfixturevalue("agent_url")
    return request.param, lambda spec: run(url, spec)


def attack_run(backend: Any, *steps: str, limits: SandboxLimits | None = None) -> ExecutionResult:
    spec = run_input(files=attack(*steps), cases=one_case(), limits=limits or SHORT)
    result: ExecutionResult = backend[1](spec)
    return result


def test_a02_t14_the_code_cannot_write_to_the_root_filesystem_or_the_runner(backend: Any) -> None:
    """Reading `/etc/passwd` shows the sandbox's own file (a read-only root); nothing can change it
    nor the runner, so there is nothing to persist to or to take over."""
    said = reported(attack_run(backend, READ_SYSTEM_FILE))
    assert "passwd-read-" in said, said  # it really read the file
    assert "read-only:/etc/passwd" in said and "read-only:/opt/sbam/harness/run_cases.py" in said
    assert "writable:" not in said, said


def test_a02_t15_the_code_cannot_open_a_socket_to_the_internet(backend: Any) -> None:
    if backend[0] == "local":
        pytest.skip("the local container has a bridge network: only the VM enforces egress (#122)")
    result = attack_run(backend, OPEN_SOCKET)
    assert "socket-connected" not in reported(result), "an undeclared connection went out"
    assert all(e.verdict is not EgressVerdict.ALLOW for e in result.egress), result.egress


def test_a02_t16_a_fork_bomb_cannot_take_the_host_and_the_next_run_is_clean(
    backend: Any,
) -> None:
    result = attack_run(backend, FORK_BOMB)
    assert result.destroy_error is None, "the sandbox could not be destroyed after the bomb"
    assert not result.classified.passed or "forked-" in reported(result)
    follow = backend[1](replace(run_input(cases=one_case(), limits=SHORT), run_id=uuid.uuid4()))
    assert follow.classified.category is None, follow.classified  # nothing left over


def test_a02_t17_a_ten_gigabyte_allocation_is_an_oom_case_not_a_dead_host(backend: Any) -> None:
    result = attack_run(backend, ALLOCATE_10GB, limits=SandboxLimits(case_timeout_s=30))
    assert result.classified.category is FailureCategory.OOM, result.classified
    assert result.destroy_error is None


def test_a02_t18_a_sleep_past_the_deadline_is_killed_as_a_timeout(backend: Any) -> None:
    result = attack_run(backend, SLEEP_PAST_DEADLINE)
    assert result.classified.category is FailureCategory.TIMEOUT, result.classified
    assert not result.classified.passed and result.destroy_error is None
