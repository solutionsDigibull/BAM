"""E13.6c against the real `promptfoo` container of the Compose stack (ADR 0024, #638).

Grading jobs go in through the shared job directory, from inside the container, the way the build
worker submits them; the grading model is the stack's Bifrost alias `sbam-rubric` (the mock upstream
under `LLM_PROVIDER=mock`, which passes a request unless it carries `MOCK_RUBRIC_FAIL`). The same
behaviour is covered without Docker by `test_e13_6_grader.py` and `test_llm_mock_rubric.py`; this
proves the built image, its read-only filesystem, its Compose wiring and the Bifrost route.

A Promptfoo outage (Bifrost down) is not run here: it would take the shared stack's gateway down. It
was checked by hand on 2026-11-02 and is covered at the unit level by the measured result shape
(`test_e13_6c_an_api_call_error_written_as_an_assertion_failure_is_still_infrastructure`).
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_contracts.promptfoo import AssertionType, SuiteAssertion, SuiteCase
from sbam_contracts.sandbox import RunnerOutputs
from sbam_contracts.testgen import CaseKind
from sbam_workers.test.grader import SidecarGrader
from sbam_workers.test.judge import PromptfooInfraError, TestingBlocked, grade_with_retries

from .conftest import Compose

pytestmark = pytest.mark.integration

PROBE = Path(__file__).parent / "probes" / "rubric_probe.mjs"


@pytest.fixture(scope="module")
def container(compose: Compose) -> str:
    cid = compose("ps", "-q", "promptfoo").stdout.strip()
    if not cid:
        pytest.skip("the promptfoo service is not part of this stack (rebuild with make up)")
    return cid


def test_e13_6c_t07_the_sidecar_gives_verdicts_through_bifrost_and_refuses_other_aliases(
    compose: Compose, container: str
) -> None:
    result = compose(
        "exec", "-T", "promptfoo", "node", "--input-type=module", "-e", PROBE.read_text("utf-8"),
        timeout=300,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr[-2000:]
    got = json.loads(result.stdout.strip().splitlines()[-1])
    assert got["graded"]["error"] is None
    assert got["graded"]["verdicts"] == {"polite": True, "rude": False}
    assert got["refused"]["verdicts"] == {}
    assert got["refused"]["error"] == "refused: the alias is not allowed"


def test_e13_6c_the_container_is_hardened_and_publishes_no_port(container: str) -> None:
    inspect = subprocess.run(  # noqa: S603 - fixed docker argv, no shell
        ["docker", "inspect", container],  # noqa: S607 - docker from PATH, like conftest
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(inspect.stdout)[0]
    assert info["HostConfig"]["ReadonlyRootfs"] is True
    assert info["HostConfig"]["CapDrop"] == ["ALL"]
    assert info["Config"]["User"] == "10001:10001"
    assert not any(info["NetworkSettings"]["Ports"].values()), "the sidecar must not be published"
    assert list(info["NetworkSettings"]["Networks"]) == [
        next(n for n in info["NetworkSettings"]["Networks"] if n.endswith("sbam_internal"))
    ]  # on the internal network only: no route to the internet


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed docker argv, no shell
        ["docker", *args],  # noqa: S607 - docker from PATH, like conftest
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture
def crashing_sidecar(container: str, tmp_path: Path) -> Iterator[Path]:
    """The real runner, from the stack's own image, whose `promptfoo` binary is `/bin/false`: the
    CLI exits 1 and writes no results file. It runs as a throwaway container on no network with the
    job directory bind-mounted, so the stack's own sidecar and gateway are not touched."""
    image = _docker("inspect", "--format", "{{.Config.Image}}", container).stdout.strip()
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    user = "10001:10001"  # a Windows bind mount has no owner to match
    if sys.platform != "win32":
        user = f"{os.getuid()}:{os.getgid()}"
    name = f"sbam-t07-{uuid.uuid4().hex[:8]}"
    started = _docker(
        "run", "-d", "--rm", "--name", name, "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--user", user,
        "--tmpfs", "/tmp:size=16m,mode=1777", "--tmpfs", "/work:size=32m,mode=1777",  # noqa: S108
        "-e", "PROMPTFOO_BIN=/bin/false", "-e", "PROMPTFOO_JOBS=/jobs", "-e", "PROMPTFOO_POLL=50",
        "-v", f"{jobs}:/jobs", image,
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"cannot start a throwaway sidecar here: {started.stderr[-200:]}")
    try:
        yield jobs
    finally:
        _docker("rm", "-f", name)


def _one_rubric_case() -> tuple[list[SuiteCase], RunnerOutputs]:
    case = SuiteCase(
        name="pass_first",
        kind=CaseKind.PASS_FIRST,
        input={},
        expected_terminal="SUCCESS",
        assertions=[
            SuiteAssertion(type=AssertionType.TERMINAL_STATE),
            SuiteAssertion(type=AssertionType.LLM_RUBRIC, value="reads well"),
        ],
    )
    trace: dict[str, Any] = {
        "case_id": "pass_first",
        "terminal": "SUCCESS",
        "iterations": 1,
        "passes": [
            {
                "pass": 1,
                "fresh_state": {},
                "action_output": {},
                "check_output": {"state_handoff_id": "h-1"},
                "action_ms": 1,
                "check_ms": 1,
                "ui_update": "Looping [Pass 1/3]",
                "gate_eval": {"success": True, "escalation": False},
            }
        ],
        "ui_updates": ["Looping [Pass 1/3]", "Status: Completed"],
        "handoff_rows": 1,
    }
    outputs = RunnerOutputs.model_validate(
        {
            "runtime_digest": "sha256:" + "a" * 64,
            "cases": [
                {
                    "case_id": "pass_first",
                    "expected_terminal": "SUCCESS",
                    "terminal": "SUCCESS",
                    "passed": True,
                    "trace": trace,
                    "handoff_rows": [{"iteration_pass": 1}],
                }
            ],
            "resources": {"wall_ms": 1, "cpu_ms": 1, "peak_rss_kb": 1},
            "mode": "CUBESANDBOX",
        }
    )
    return [case], outputs


def test_e13_6_t07_promptfoo_exiting_1_without_results_is_an_infra_error_then_testing_is_blocked(
    crashing_sidecar: Path,
) -> None:
    cases, outputs = _one_rubric_case()
    grader = SidecarGrader(crashing_sidecar, timeout=60, poll=0.1)

    # one job: the runner's own message for a CLI that wrote no results file, never a verdict
    with pytest.raises(PromptfooInfraError, match="promptfoo exited with 1 and wrote no results"):
        asyncio.run(grader(cases, outputs))
    assert list(crashing_sidecar.iterdir()) == []  # the grader removed its job

    # the judge's retry rule: three jobs, then TESTING_BLOCKED, never "the rubric was not met"
    with pytest.raises(TestingBlocked):
        asyncio.run(grade_with_retries(grader, cases, outputs))
