"""E13.6: the rubric grader client against a fake sidecar (a thread on a temp jobs directory)."""

import asyncio
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sbam_contracts.testgen import CaseKind
from sbam_workers.test.grader import ALIAS, MAX_OUTPUT_CHARS, SidecarGrader, output_text
from sbam_workers.test.judge import (
    PromptfooInfraError,
    TestingBlocked,
    judge_with_grader,
)
from test_e13_6_judge import outputs, suite

Reply = Callable[[dict[str, Any]], dict[str, Any] | None]


class Sidecar:
    """Answers each READY job with `reply(request)`; a `None` reply writes DONE with no result."""

    def __init__(self, jobs: Path, reply: Reply) -> None:
        self.jobs, self.reply = jobs, reply
        self.requests: list[dict[str, Any]] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self) -> None:
        while not self.stop.is_set():
            try:
                jobs = list(self.jobs.iterdir())
            except OSError:  # the test's directory is gone: nothing left to answer
                return
            for job in jobs:
                try:
                    self.take(job)
                except OSError:  # the client already removed the finished job
                    continue
            time.sleep(0.01)

    def take(self, job: Path) -> None:
        if (job / "READY").exists() and not (job / "DONE").exists():
            request = json.loads((job / "request.json").read_text(encoding="utf-8"))
            self.requests.append(request)
            answer = self.reply(request)
            if answer is not None:
                (job / "result.json").write_text(json.dumps(answer), encoding="utf-8")
            (job / "DONE").touch()


@pytest.fixture
def jobs(tmp_path: Path) -> Path:
    path = tmp_path / "jobs"
    path.mkdir()
    return path


@pytest.fixture
def start(jobs: Path) -> Iterator[Callable[[Reply], Sidecar]]:
    started: list[Sidecar] = []

    def make(reply: Reply) -> Sidecar:
        side = Sidecar(jobs, reply)
        side.thread.start()
        started.append(side)
        return side

    yield make
    for side in started:
        side.stop.set()
        side.thread.join(2)


def all_pass(request: dict[str, Any]) -> dict[str, Any]:
    return {"verdicts": {c["name"]: True for c in request["cases"]}, "error": None}


def cases_with_rubric() -> Any:
    return [c for c in suite(CaseKind.PASS_FIRST).cases if c.kind is CaseKind.PASS_FIRST]


def grade(grader: SidecarGrader) -> Any:
    return asyncio.run(grader(cases_with_rubric(), outputs()))


def test_e13_6_a_verdict_comes_back_for_each_case_asked_about(jobs: Path, start: Any) -> None:
    side = start(all_pass)
    assert grade(SidecarGrader(jobs, timeout=30)) == {"pass_first": True}
    [request] = side.requests
    assert request["alias"] == ALIAS == "sbam-rubric"
    [asked] = request["cases"]
    assert asked["name"] == "pass_first" and asked["rubric"] == "reads well"
    assert "state_handoff_id" in asked["output"]


def test_e13_6_a_false_verdict_is_a_verdict_not_an_error(jobs: Path, start: Any) -> None:
    def fail(request: dict[str, Any]) -> dict[str, Any]:
        return {"verdicts": {c["name"]: False for c in request["cases"]}}

    start(fail)
    assert grade(SidecarGrader(jobs, timeout=30)) == {"pass_first": False}


@pytest.mark.parametrize(
    "reply",
    [
        lambda _r: None,  # DONE with no result.json (promptfoo exited 1 without results)
        lambda _r: {"error": "promptfoo exited with 1"},
        lambda _r: {"verdicts": {}},  # a verdict missing for the case
        lambda _r: {"verdicts": {"pass_first": "yes"}},  # not a boolean
        lambda _r: {"verdicts": ["pass_first"]},
    ],
)
def test_e13_6_t07_anything_but_a_clean_answer_is_an_infra_error(
    jobs: Path, reply: Reply, start: Any
) -> None:
    start(reply)
    with pytest.raises(PromptfooInfraError):
        grade(SidecarGrader(jobs, timeout=30))


def test_e13_6_a_sidecar_that_never_answers_times_out_as_an_infra_error(jobs: Path) -> None:
    ticks = iter(range(1000))
    grader = SidecarGrader(jobs, timeout=3, clock=lambda: float(next(ticks)), sleep=lambda _s: None)
    with pytest.raises(PromptfooInfraError, match="did not answer in time"):
        grade(grader)
    assert list(jobs.iterdir()) == []


def test_e13_6_an_unmounted_jobs_directory_is_an_infra_error(tmp_path: Path) -> None:
    with pytest.raises(PromptfooInfraError, match="not mounted"):
        grade(SidecarGrader(tmp_path / "missing", timeout=1))
    assert not (tmp_path / "missing").exists()  # never invented


def test_e13_6_unreadable_json_is_an_infra_error(jobs: Path) -> None:
    def garbage() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for job in jobs.iterdir():
                if (job / "READY").exists():
                    (job / "result.json").write_text("{not json", encoding="utf-8")
                    (job / "DONE").touch()
                    return
            time.sleep(0.01)

    thread = threading.Thread(target=garbage, daemon=True)
    thread.start()
    with pytest.raises(PromptfooInfraError, match="unreadable"):
        grade(SidecarGrader(jobs, timeout=30))
    thread.join(2)


def test_e13_6_the_output_the_grader_reads_is_capped() -> None:
    assert len(output_text({"text": "x" * (MAX_OUTPUT_CHARS * 2)})) == MAX_OUTPUT_CHARS


def test_e13_6_t07_a_sidecar_that_keeps_failing_blocks_testing_after_three_jobs(
    jobs: Path, start: Any
) -> None:
    side = start(lambda _r: {"error": "exit 1"})
    grader = SidecarGrader(jobs, timeout=30)
    with pytest.raises(TestingBlocked):
        asyncio.run(judge_with_grader(suite(CaseKind.PASS_FIRST), outputs(), grader))
    assert len(side.requests) == 3


def test_e13_6_the_judge_uses_the_sidecars_verdict(jobs: Path, start: Any) -> None:
    start(all_pass)
    grader = SidecarGrader(jobs, timeout=30)
    result = asyncio.run(judge_with_grader(suite(CaseKind.PASS_FIRST), outputs(), grader))
    assert result.green and result.passed == 3
