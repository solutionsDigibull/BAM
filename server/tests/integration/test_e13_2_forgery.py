"""#625: generated code cannot forge its own test results.

The in-VM runner is the trusted side: it never imports the bundle, which runs in a child process of
its own (ADR 0027). These tests run bundles that try to forge a verdict inside the real hardened
`sandbox-runner` container, through the real agent and executor, and read back what the platform
concluded. Each attack is one a bundle written by a prompt-injected model could make; none may end
as a pass. The runner's behaviour on honest bundles (timeouts, OOM, import errors) stays covered by
`test_e13_2_runner_container.py`.

Needs Docker and `skill-runtime:local` (`make runtime-registry`); not part of the unit run.
"""

import textwrap
from typing import Any

from sbam_contracts.sandbox import FailureCategory, InfraReason, RunnerOutputs

from .test_e13_2_runner_container import (  # noqa: F401 - agent_url is a fixture
    agent_url,
    case,
    insert_auth,
    run,
    run_input,
)

FORGED = b'{"forged": true}'

# What every attacking `action_tool.py` ends with: an honest handoff row and a report of what each
# attempt did (in `jwt_token`), so the test can read the outcomes back from the recorded passes.
HEAD = """\
from __future__ import annotations

import os
import signal
import subprocess
import sys

from . import state_handoff


def auth_mcp_session_otp_evaluator(*, mobile_number, otp_payload, device_fingerprint, **_):
    results = []
    out = os.path.join(os.getcwd(), "..", "out")
    forged = os.path.join(out, "outputs.json")
"""
TAIL = """\
    state_handoff.append(
        iteration_pass=1, device_fingerprint=device_fingerprint, otp_match=True, access_scope="none"
    )
    return {
        "auth_status": "DENIED",
        "user_role": None,
        "jwt_token": ",".join(results),
        "state_handoff_id": None,
    }
"""

WRITER_SOURCE = """\
import sys, time
for _ in range(600):
    open(sys.argv[1], "wb").write(b'{"forged": true}')
    time.sleep(0.05)
"""

FORGE_FILE = """\
os.makedirs(out, exist_ok=True)
open(forged, "wb").write(b'{"forged": true}')
results.append("wrote-forged-outputs")
"""
PROBE_RUNNER = """\
in_process = any("sbam_workers" in name for name in sys.modules)
results.append("harness-in-process" if in_process else "harness-absent")
try:
    os.open("/proc/%d/mem" % os.getppid(), os.O_RDWR)
    results.append("runner-memory-open")
except OSError:
    results.append("runner-memory-denied")
try:
    open("/opt/sbam/harness/run_cases.py", "ab").close()
    results.append("harness-on-disk-writable")
except OSError:
    results.append("harness-on-disk-denied")
"""
LATE_WRITER = f"""\
subprocess.Popen(
    [sys.executable, "-c", {WRITER_SOURCE!r}, forged],
    start_new_session=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
results.append("late-writer-started")
"""
KILL_RUNNER = """\
os.kill(os.getppid(), signal.SIGKILL)
results.append("killed-runner")
"""
REPLACE_OUT = """\
import shutil
shutil.rmtree(out, ignore_errors=True)
os.symlink("/nonexistent-target", out)
results.append("out-replaced")
"""


def attack(*steps: str) -> dict[str, str]:
    """The bundle file that runs `steps` inside the action tool."""
    body = "".join(textwrap.indent(step, "    ") for step in steps)
    return {"action_tool.py": HEAD + body + TAIL}


def one_case() -> list[Any]:
    return [case("pass-first", "SUCCESS", insert_auth(0, "active"))]


def reported(result: Any) -> str:
    """What the attack says it managed, from the action output the runner recorded."""
    outputs = RunnerOutputs.model_validate_json(result.outputs_json or b"")
    passes: Any = outputs.cases[0].trace["passes"]
    return ",".join(p["action_output"].get("jwt_token", "") for p in passes)


def test_e13_2_625_a_bundle_cannot_reach_the_runner_nor_leave_a_writer_behind(
    agent_url: str,  # noqa: F811
) -> None:
    """In-process monkeypatching is gone (the harness is not in the bundle's process), the runner's
    memory and the harness files are closed to it, and a process it leaves running is stopped before
    the runner writes its file, so the forged file never survives."""
    result = run(
        agent_url,
        run_input(files=attack(FORGE_FILE, PROBE_RUNNER, LATE_WRITER), cases=one_case()),
    )
    said = reported(result)
    assert "wrote-forged-outputs" in said and "late-writer-started" in said, said  # it really tried
    assert "harness-absent" in said and "harness-in-process" not in said
    assert "runner-memory-denied" in said and "runner-memory-open" not in said
    assert "harness-on-disk-denied" in said and "harness-on-disk-writable" not in said
    assert FORGED not in (result.outputs_json or b""), "a forged outputs.json survived"
    assert result.exit_code == 0 and result.destroy_error is None
    assert not result.classified.passed, result.classified  # SUCCESS was never reached honestly


def test_e13_2_625_ending_the_runner_after_writing_a_forged_file_is_not_a_pass(
    agent_url: str,  # noqa: F811
) -> None:
    """Same user, so the bundle can kill the runner. It cannot make it exit 0: a file left by a
    runner that did not exit cleanly is not evidence (classify), so this ends as infrastructure.
    """
    result = run(agent_url, run_input(files=attack(FORGE_FILE, KILL_RUNNER), cases=one_case()))
    assert result.exit_code != 0
    assert not result.classified.passed
    assert result.classified.category is FailureCategory.INFRA_ERROR
    assert result.classified.infra_reason is InfraReason.RUNNER_FAILED


def test_e13_2_625_a_bundle_that_replaces_the_out_directory_cannot_pass_either(
    agent_url: str,  # noqa: F811
) -> None:
    """A symlink where the directory belongs: the runner clears it before writing, and when it still
    cannot write its file the run is a failed runner, never a verdict."""
    result = run(agent_url, run_input(files=attack(REPLACE_OUT), cases=one_case()))
    assert not result.classified.passed, result.classified
