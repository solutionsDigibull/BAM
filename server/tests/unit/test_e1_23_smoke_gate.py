"""E1.23 / #375: scripts/ci-smoke-gate.sh on a throwaway git history, with a fake `gh` that lists
earlier `ci` runs. A dev run cancelled while queued leaves its changes to the next run."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
GATE = REPO / "scripts" / "ci-smoke-gate.sh"
BASH = shutil.which("bash")
GIT = shutil.which("git") or "git"
pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt", reason="bash on Linux (CI)")

# `gh run list ... --jq Q` prints the fake runs through jq, like the real one would.
FAKE_GH = r"""#!/usr/bin/env bash
q=""; while [ $# -gt 0 ]; do [ "$1" = --jq ] && q=$2; shift; done
printf '%s' "$FAKE_RUNS" | jq -r "$q"
"""


class History:
    def __init__(self, tmp: Path) -> None:
        self.dir = tmp / "repo"
        self.dir.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.git("init", "-q", "-b", "dev")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def git(self, *args: str) -> str:
        out = subprocess.run(  # noqa: S603 - fixed argv, test-owned repo
            [GIT, *args], cwd=self.dir, check=True, capture_output=True, text=True
        )
        return out.stdout.strip()

    def commit(self, path: str) -> str:
        file = self.dir / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"{path} {self.git('rev-list', '--all', '--count') or 0}\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", path)
        return self.git("rev-parse", "HEAD")

    def fake_gh(self, runs: list[dict[str, object]]) -> dict[str, str]:
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
        gh.chmod(0o755)
        return {"GH_TOKEN": "t", "FAKE_RUNS": json.dumps(runs), "GITHUB_REPOSITORY": "o/r"}

    def gate(self, sha: str, before: str, event: str = "push", **env: str) -> str:
        path = f"{self.bin}:{os.environ['PATH']}"
        base = {"PATH": path, "EVENT": event, "REF": "refs/heads/dev", "BEFORE": before}
        out = subprocess.run(  # noqa: S603 - fixed argv, test-owned paths
            [BASH or "bash", str(GATE), sha],
            cwd=self.dir,
            env={**base, **env, "GITHUB_RUN_ID": env.get("GITHUB_RUN_ID", "99")},
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout


def run(sha: str, run_id: int, conclusion: str) -> dict[str, object]:
    return {"databaseId": run_id, "headSha": sha, "conclusion": conclusion}


@pytest.fixture
def h(tmp_path: Path) -> History:
    h = History(tmp_path)
    h.commit("README.md")
    return h


def test_375_t01_cancelled_stack_change_runs_on_the_next_push(h: History) -> None:
    covered = h.git("rev-parse", "HEAD")
    a = h.commit("compose/compose.yaml")  # its queued run was cancelled
    b = h.commit("server/app.py")
    env = h.fake_gh([run(a, 2, "cancelled"), run(covered, 1, "success")])
    out = h.gate(b, before=a, **env)
    assert "run=true" in out
    assert "touches the stack (since " in out and "compose/compose.yaml" in out


def test_375_t02_a_covered_stack_change_is_not_rerun(h: History) -> None:
    a = h.commit("compose/compose.yaml")
    b = h.commit("server/app.py")
    out = h.gate(b, before=a, **h.fake_gh([run(a, 2, "success")]))
    assert "run=false" in out


def test_375_t03_without_gh_the_push_range_is_used(h: History) -> None:
    a = h.commit("compose/compose.yaml")
    b = h.commit("server/app.py")
    assert "run=false" in h.gate(b, before=a)  # no GH_TOKEN: today's behaviour


def test_375_t04_own_run_newer_runs_and_in_progress_runs(h: History) -> None:
    a = h.commit("compose/compose.yaml")
    b = h.commit("server/app.py")
    h.git("checkout", "-q", "-b", "other", a)
    stray = h.commit("x.py")  # not an ancestor of b
    h.git("checkout", "-q", "dev")
    runs = [run(stray, 4, "success"), run(b, 99, ""), run(a, 2, "")]  # 99 = this run
    assert "run=false" in h.gate(b, before=a, **h.fake_gh(runs))  # a's run is in progress


def test_375_t05_cancelled_tenth_merge_is_caught_up(h: History) -> None:
    for i in range(8):
        h.commit(f"src/{i}.py")  # merges #2..#9
    covered = h.git("rev-parse", "HEAD")
    tenth = h.commit("src/ten.py")  # #10, cancelled
    eleventh = h.commit("src/eleven.py")  # #11
    env = h.fake_gh([run(tenth, 2, "cancelled"), run(covered, 1, "success")])
    out = h.gate(eleventh, before=tenth, **env)
    assert "run=true" in out and "every 10th (since " in out
    assert "run=false" in h.gate(eleventh, before=tenth)  # the old gate missed it


@pytest.mark.parametrize(
    ("event", "ref", "expected"),
    [
        ("pull_request", "refs/heads/dev", "run=false"),
        ("push", "refs/heads/main", "run=true"),
        ("schedule", "refs/heads/dev", "run=true"),
        ("workflow_dispatch", "refs/heads/dev", "run=true"),
    ],
)
def test_e1_23_other_events_are_unchanged(h: History, event: str, ref: str, expected: str) -> None:
    sha = h.git("rev-parse", "HEAD")
    assert expected in h.gate(sha, before="", event=event, REF=ref)
