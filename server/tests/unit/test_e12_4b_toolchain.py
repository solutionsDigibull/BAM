"""E12.4b: the static tools through the sidecar protocol, against the real runner code (no Docker).

The runner (compose/docker/toolchain/runner.py) is loaded from the repository and driven in a
thread over a temporary jobs directory, so the worker's `SidecarExecutor` and the runner are tested
together. The container's isolation (no network, read-only, non-root) is proved against the real
image in tests/integration/test_e12_4b_toolchain_container.py.
"""

import importlib.util
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sbam_workers.build.gates.static_tools import make_tools, run_bandit, run_mypy, run_ruff
from sbam_workers.build.gates.toolchain import (
    COMMANDS,
    LocalExecutor,
    SidecarExecutor,
    ToolFailure,
)

RUNNER = Path(__file__).resolve().parents[3] / "compose" / "docker" / "toolchain" / "runner.py"
SOURCE = {"action.py": b"import os\n\n\ndef action(value):\n    unused = 1\n    return value\n"}


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("toolchain_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner() -> ModuleType:
    return load_runner()


@pytest.fixture
def sidecar(runner: ModuleType, tmp_path: Path) -> Iterator[SidecarExecutor]:
    """A runner thread over `tmp_path/jobs`, and an executor writing jobs there."""
    jobs, scratch = tmp_path / "jobs", tmp_path / "scratch"
    jobs.mkdir()
    scratch.mkdir()
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            for job in runner.pending(jobs):
                runner.finish(job, runner.run_job(job, scratch, 120.0))
            time.sleep(0.05)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield SidecarExecutor(jobs, timeout=120.0, poll=0.05)
    finally:
        stop.set()
        thread.join(5)


def test_e12_4b_the_sidecar_gives_the_same_findings_versions_and_hashes_as_the_local_executor(
    sidecar: SidecarExecutor,
) -> None:
    local = LocalExecutor()
    for tool in (run_ruff, run_mypy, run_bandit):
        via_sidecar, via_local = tool(SOURCE, sidecar), tool(SOURCE, local)
        assert via_sidecar == via_local, tool.__name__


def test_e12_4b_a_failing_bundle_is_a_failed_verdict_through_the_sidecar(
    sidecar: SidecarExecutor,
) -> None:
    ruff = run_ruff(SOURCE, sidecar)
    assert ruff.status.value == "FAILED" and {"F401", "F841"} <= {f["code"] for f in ruff.findings}
    assert run_mypy(SOURCE, sidecar).status.value == "FAILED"


def test_e12_4b_the_tools_made_for_the_worker_run_through_the_given_executor(
    sidecar: SidecarExecutor,
) -> None:
    ruff, black, mypy, bandit = make_tools(sidecar)
    assert [t(SOURCE).gate.value for t in (ruff, black, mypy, bandit)] == [
        "RUFF",
        "BLACK",
        "MYPY",
        "BANDIT",
    ]


def test_e12_4b_the_runner_and_the_worker_use_the_same_command_table(runner: ModuleType) -> None:
    assert set(runner.COMMANDS) == set(COMMANDS) == {"ruff", "mypy", "bandit"}
    for tool, argv in COMMANDS.items():
        assert runner.COMMANDS[tool][1:] == list(argv), tool  # [0] is each side's own python


def test_e12_4b_a_job_is_only_taken_when_it_is_complete_and_a_result_only_when_done(
    runner: ModuleType, tmp_path: Path
) -> None:
    job = tmp_path / "j1"
    (job / "files").mkdir(parents=True)
    assert runner.pending(tmp_path) == []  # no READY yet
    (job / "READY").touch()
    assert runner.pending(tmp_path) == [job]
    runner.finish(job, {"returncode": 0, "stdout": "", "error": None})
    assert (job / "result.json").exists() and (job / "DONE").exists()
    assert runner.pending(tmp_path) == []  # done jobs are not taken again


@pytest.mark.parametrize(
    "name",
    ["../x.py", "/etc/passwd", "a/../../b.py", "a//b.py", "./a.py", "a b.py", "", "x" * 300],
)
def test_e12_4b_the_runner_refuses_a_file_name_that_could_leave_the_scratch_directory(
    runner: ModuleType, name: str
) -> None:
    with pytest.raises(runner.RefusedError):
        runner.safe_name(name)


def test_e12_4b_the_runner_accepts_ordinary_bundle_names(runner: ModuleType) -> None:
    for name in ("action.py", "skill/handoff.py", "a-b/c_d.py"):
        assert runner.safe_name(name) == name


def make_job(root: Path, request: Any, files: dict[str, bytes]) -> Path:
    job = root / "job"
    (job / "files").mkdir(parents=True)
    for name, data in files.items():
        target = job / "files" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (job / "request.json").write_text(json.dumps(request))
    return job


def test_e12_4b_the_runner_only_runs_the_tools_in_its_table(
    runner: ModuleType, tmp_path: Path
) -> None:
    requests: list[Any] = [{"tool": "python"}, {"tool": "sh -c 'id'"}, {"tool": ["ruff"]}, {}, []]
    for request in requests:
        job = make_job(tmp_path / str(abs(hash(json.dumps(request)))), request, SOURCE)
        result = runner.run_job(job, tmp_path, 5.0)
        assert result["error"] and str(result["error"]).startswith("refused"), request
        assert result["returncode"] is None


def test_e12_4b_the_runner_refuses_a_job_with_no_files_or_unreadable(
    runner: ModuleType, tmp_path: Path
) -> None:
    empty = make_job(tmp_path / "a", {"tool": "ruff"}, {})
    assert str(runner.run_job(empty, tmp_path, 5.0)["error"]).startswith("refused")
    broken = tmp_path / "b" / "job"
    (broken / "files").mkdir(parents=True)
    (broken / "request.json").write_text("{not json")
    assert str(runner.run_job(broken, tmp_path, 5.0)["error"]).startswith("refused")


def test_e12_4b_the_runner_does_not_execute_the_bundle(runner: ModuleType, tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    hostile = f"import os\nos.system('touch {marker.as_posix()}')\nopen({str(marker)!r}, 'w')\n"
    job = make_job(tmp_path / "h", {"tool": "ruff"}, {"evil.py": hostile.encode()})
    (job / "config").write_text('line-length = 100\n[lint]\nselect = ["E", "F"]\n')
    result = runner.run_job(job, tmp_path, 60.0)
    assert result["error"] is None and not marker.exists()  # it was read, never run


def test_e12_4b_a_runner_that_never_answers_is_an_infrastructure_failure(tmp_path: Path) -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    executor = SidecarExecutor(tmp_path, timeout=10.0, poll=1.0, clock=clock, sleep=sleep)
    with pytest.raises(ToolFailure, match="did not answer"):
        executor.run("ruff", SOURCE, "line-length = 100\n")
    assert list(tmp_path.iterdir()) == []  # the abandoned job is cleaned up


def test_e12_4b_an_answer_that_is_an_error_or_unreadable_or_a_crash_gives_no_verdict(
    tmp_path: Path,
) -> None:
    def answering(payload: str) -> SidecarExecutor:
        class Fake(SidecarExecutor):
            def _write(self, job: Path, tool: str, files: Any, config: Any) -> None:
                (job / "result.json").write_text(payload)
                (job / "DONE").touch()

        return Fake(tmp_path, timeout=5.0, poll=0.01)

    refused = json.dumps({"returncode": None, "stdout": "", "error": "refused: x"})
    crashed = json.dumps({"returncode": 2, "stdout": "", "error": None, "version": "1"})
    for payload in (refused, crashed, "{not json"):
        with pytest.raises(ToolFailure):
            answering(payload).run("ruff", SOURCE, None)
    ok = json.dumps({"returncode": 1, "stdout": "[]", "error": None, "version": "9.9"})
    out = answering(ok).run("ruff", SOURCE, None)
    assert (out.stdout, out.version) == ("[]", "9.9")  # exit 1 is "found something", not a crash


def test_e12_4b_a_jobs_directory_that_is_missing_is_an_infrastructure_failure(
    tmp_path: Path,
) -> None:
    executor = SidecarExecutor(tmp_path / "not-mounted" / "deeper", timeout=1.0, poll=0.01)
    # mkdir(parents=True) would invent the directory: a worker whose volume is not mounted must
    # not silently write into its own filesystem and then wait for a runner that cannot see it
    with pytest.raises(ToolFailure):
        executor.run("bandit", SOURCE, None)


@pytest.mark.parametrize(
    ("jobs_dir", "expected"), [("", LocalExecutor), ("{tmp}", SidecarExecutor)]
)
def test_e12_4b_the_worker_uses_the_sidecar_only_when_a_jobs_directory_is_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    jobs_dir: str,
    expected: type,
) -> None:
    from llm_db import settings
    from pipeline_db import pipeline_sessions
    from sbam_workers import mirror
    from sbam_workers.build import wiring

    sessions = pipeline_sessions()
    conf = settings(storage_root=tmp_path, toolchain_jobs_dir=jobs_dir.format(tmp=tmp_path))
    monkeypatch.setattr(wiring, "WorkerSettings", lambda: conf)
    monkeypatch.setattr(mirror, "sessions", lambda: sessions)
    wiring._static_gate.cache_clear()
    try:
        gate = wiring._static_gate()
        ruff = gate.tools[0]
        assert isinstance(ruff.keywords["executor"], expected)  # type: ignore[attr-defined]
    finally:
        wiring._static_gate.cache_clear()


def test_e12_4b_a_runaway_tool_is_stopped_by_the_runner_and_gives_no_verdict(
    runner: ModuleType, tmp_path: Path
) -> None:
    job = make_job(tmp_path / "slow", {"tool": "mypy"}, SOURCE)
    (job / "config").write_text("[mypy]\nstrict = True\n")
    result = runner.run_job(job, tmp_path, 0.001)  # far less time than any tool needs to start
    assert result == {"returncode": None, "stdout": "", "error": "timeout"}

    class Answered(SidecarExecutor):
        def _write(self, job_dir: Path, tool: str, files: Any, config: Any) -> None:
            (job_dir / "result.json").write_text(json.dumps(result))
            (job_dir / "DONE").touch()

    with pytest.raises(ToolFailure, match="timeout"):  # STATIC_GATE_INFRA: retried, never a pass
        Answered(tmp_path, timeout=5.0, poll=0.01).run("mypy", SOURCE, None)
