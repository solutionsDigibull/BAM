"""WG-31 (#908) on the staging-runtime stack: `scripts/staging-proof.sh` end to end, mock LLM only.

T02 Staging RUNNING (UI served to the assigned FDE only, MCP forwarded), T03 an undeclared host is
refused and logged, T04 a newer version supersedes the old deployment, T05b the socket proxy refuses
exec, a privileged create and a bind mount. T01 and T05 (no docker.sock in a worker) are also
checked statically in tests/unit/test_wg31_staging_compose.py.

Needs `make up PROFILES=staging-runtime` (and bash, docker, `LLM_PROVIDER=mock`); skipped when that
stack is not running (the script exits 2 for a missing precondition).
"""

import os
import re
import shutil
import subprocess

import pytest

from .conftest import ENV_FILE, REPO

pytestmark = pytest.mark.integration

CASES = (
    "WG31-T01a",
    "WG31-T02",
    "WG31-T02f",
    "WG31-T03",
    "WG31-T03c",
    "WG31-T04",
    "WG31-T05",
    "WG31-T05b",
)


def test_wg31_the_staging_proof_passes_on_the_stack() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    run = subprocess.run(  # noqa: S603 - a fixed script, no shell
        [bash, str(REPO / "scripts/staging-proof.sh")],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        env={**os.environ, "ENV_FILE": ENV_FILE},
    )
    if run.returncode == 2:
        pytest.skip(run.stderr.strip()[-200:] or "the staging-runtime stack is not up")
    results = dict(re.findall(r"^CHECK (\S+) (PASS|FAIL)", run.stdout, re.MULTILINE))
    failed = {case: status for case, status in results.items() if status != "PASS"}
    assert run.returncode == 0 and not failed, run.stdout[-3000:]
    assert all(case in results for case in CASES), results
