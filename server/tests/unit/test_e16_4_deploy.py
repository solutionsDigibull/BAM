"""E16.4: scripts/deploy.sh against a fake `docker` (calls recorded, health scripted)."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from sbam_workers import bootstrap

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "scripts" / "deploy.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

OK = "sbam-api running healthy 0\napi-migrate exited  0\n"
SICK = "sbam-api running unhealthy 0\napi-migrate exited  0\n"
CRASHED = "worker-llm exited  1\n"

# Records every call; `ps` answers the next scripted health check; api-migrate exits
# FAKE_MIGRATE_RC.
FAKE_DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_DIR/calls"
case "$*" in
  *" ps -a "*)
    n=$(cat "$FAKE_DIR/n" 2>/dev/null || echo 0); echo $((n + 1)) >"$FAKE_DIR/n"
    f="$FAKE_DIR/ps.$n"; [[ -f $f ]] && cat "$f" || cat "$FAKE_DIR/ps.last" ;;
  *"run --rm --no-deps api-migrate"*) echo "alembic: at head"; exit "${FAKE_MIGRATE_RC:-0}" ;;
esac
"""
FAKE_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$FAKE_DIR/curl"
"""


def digest(c: str) -> str:
    return f"ghcr.io/solutionsdigibull/sbam-api@sha256:{c * 64}"


class Vm:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.root = tmp / "releases"
        self.fake = tmp / "fake"
        self.fake.mkdir()
        for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
            (self.fake / name).write_text(body, encoding="utf-8", newline="\n")
            (self.fake / name).chmod(0o755)
        (tmp / "dev.env").write_text("SBAM_PORTAL_HOST=x\n", encoding="utf-8")
        (tmp / "bundle" / "compose").mkdir(parents=True)
        (tmp / "bundle" / "compose" / "compose.yaml").write_text("name: sbam\n", encoding="utf-8")

    def running(self, rid: str, c: str) -> None:
        (self.root / rid).mkdir(parents=True)
        (self.root / rid / "release.env").write_text(f"SBAM_API_IMAGE={digest(c)}\n")
        (self.root / "current").write_text(f"{rid}\n")

    def deploy(
        self, rid: str, image: str, checks: list[str], **env: str
    ) -> subprocess.CompletedProcess[str]:
        release = self.tmp / f"{rid}.env"
        release.write_text(f"# release {rid}\nSBAM_API_IMAGE={image}\n", encoding="utf-8")
        for i, out in enumerate(checks):
            (self.fake / f"ps.{i}").write_text(out, encoding="utf-8")
        (self.fake / "ps.last").write_text(checks[-1] if checks else OK, encoding="utf-8")
        return subprocess.run(  # noqa: S603 - fixed argv, test-owned paths
            [
                *(BASH or "bash", str(DEPLOY), "--env", "dev", "--id", rid),
                *("--release", str(release), "--bundle", str(self.tmp / "bundle")),
            ],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "PATH": f"{self.fake}{os.pathsep}{os.environ['PATH']}",
                "FAKE_DIR": str(self.fake),
                "SBAM_RELEASES": str(self.root),
                "SBAM_ENV_FILE": str(self.tmp / "dev.env"),
                "SBAM_DEPLOY_ACTOR": "ci",
                "WATCH_CHECKS": str(len(checks)),
                "WATCH_INTERVAL": "0",
                **env,
            },
        )

    def calls(self) -> list[str]:
        path = self.fake / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def ups(self) -> list[str]:
        """The release each `up` ran with."""
        found = (re.search(r"releases[/\\](\S+?)[/\\]release\.env", c) for c in self.calls())
        return [m.group(1) for c, m in zip(self.calls(), found, strict=True) if " up -d" in c and m]

    def current(self) -> str:
        return (self.root / "current").read_text().strip()

    def log(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in (self.root / "log.jsonl").read_text().splitlines()]


@pytest.fixture
def vm(tmp_path: Path) -> Vm:
    return Vm(tmp_path)


def test_e16_4_t01_deploy_pulls_migrates_rolls_out_and_records(vm: Vm) -> None:
    vm.running("v1.0.0", "a")
    out = vm.deploy("v1.1.0", digest("b"), [OK] * 3)

    assert out.returncode == 0, out.stderr
    step = {" pull ": "pull", " run --rm --no-deps api-migrate": "migrate", " up -d ": "up"}
    steps = [s for c in vm.calls() for k, s in step.items() if k in f"{c} "]
    assert steps == ["pull", "migrate", "up"]
    assert vm.ups() == ["v1.1.0"] and vm.current() == "v1.1.0"
    record = vm.log()[-1]
    assert record["result"] == "deployed" and record["previous"] == "v1.0.0"
    assert record["actor"] == "ci" and record["images"] == {"SBAM_API_IMAGE": digest("b")}
    assert (vm.root / "v1.1.0" / "compose" / "compose.yaml").exists()
    assert "at head" in (vm.root / "v1.1.0" / "migrate.log").read_text()


def test_e16_4_t03_three_bad_checks_roll_back_to_the_previous_release(vm: Vm) -> None:
    vm.running("v1.0.0", "a")
    out = vm.deploy("v1.1.0", digest("b"), [SICK] * 3, SBAM_ALERT_WEBHOOK_URL="http://hook/x")

    assert out.returncode == 2
    assert vm.ups() == ["v1.1.0", "v1.0.0"] and vm.current() == "v1.0.0"
    assert vm.log()[-1]["result"] == "rolled_back"
    assert "rolled back to v1.0.0" in (vm.fake / "curl").read_text()


def test_e16_4_t03_a_crashed_container_counts_as_failed(vm: Vm) -> None:
    vm.running("v1.0.0", "a")
    assert vm.deploy("v1.1.0", digest("b"), [CRASHED] * 3).returncode == 2


def test_e16_4_a_flapping_check_does_not_roll_back(vm: Vm) -> None:
    vm.running("v1.0.0", "a")
    out = vm.deploy("v1.1.0", digest("b"), [SICK, SICK, OK, SICK, SICK, OK])

    assert out.returncode == 0, out.stderr
    assert vm.ups() == ["v1.1.0"] and vm.current() == "v1.1.0"


def test_e16_4_t04_failed_migration_aborts_before_any_rollout(vm: Vm) -> None:
    vm.running("v1.0.0", "a")
    out = vm.deploy("v1.1.0", digest("b"), [OK], FAKE_MIGRATE_RC="1")

    assert out.returncode == 1 and "api-migrate failed" in out.stderr
    assert vm.ups() == [] and vm.current() == "v1.0.0"
    assert vm.log()[-1]["result"] == "migration_failed"


def test_e16_4_t06_same_digests_are_a_no_op(vm: Vm) -> None:
    vm.running("v1.0.0", "a")
    out = vm.deploy("v1.0.0-rerun", digest("a"), [OK])

    assert out.returncode == 0 and "already running" in out.stdout
    assert vm.calls() == [] and vm.current() == "v1.0.0"


def test_e16_4_first_release_that_fails_has_nothing_to_roll_back(vm: Vm) -> None:
    out = vm.deploy("v1.0.0", digest("a"), [SICK] * 3)

    assert out.returncode == 1 and "no previous release" in out.stderr
    assert vm.ups() == ["v1.0.0"] and not (vm.root / "current").exists()


@pytest.mark.parametrize(
    "image",
    [
        digest("0"),  # the template's placeholder
        "ghcr.io/solutionsdigibull/sbam-api:latest",
        "ghcr.io/solutionsdigibull/sbam-api@sha256:abc",
    ],
)
def test_e16_4_only_real_digests_are_deployed(vm: Vm, image: str) -> None:
    out = vm.deploy("v1.1.0", image, [OK])

    assert out.returncode == 1 and "SBAM_API_IMAGE" in out.stderr
    assert vm.calls() == []


def test_e16_4_a_release_id_is_never_reused_for_other_digests(vm: Vm) -> None:
    vm.running("v1.0.0", "a")
    assert vm.deploy("v1.1.0", digest("b"), [OK]).returncode == 0
    out = vm.deploy("v1.1.0", digest("c"), [OK])
    assert out.returncode == 1 and "use a new id" in out.stderr


def test_e16_4_never_touches_volumes_or_takes_the_stack_down(vm: Vm) -> None:
    vm.running("v1.0.0", "a")
    vm.deploy("v1.1.0", digest("b"), [SICK] * 3)
    for call in vm.calls():
        assert not re.search(r"\b(down|volume|rm -v)\b| -v\b|--volumes", call), call
    text = DEPLOY.read_text(encoding="utf-8")
    assert not re.search(r"compose\"? [^#\n]*\b(down|volume)\b", text)


def test_e16_4_t07_workers_drain_before_the_container_is_killed() -> None:
    deploy = (REPO / "compose" / "compose.deploy.yaml").read_text(encoding="utf-8")
    grace = re.search(r"stop_grace_period: (\d+)s", deploy)
    assert grace and int(grace.group(1)) > bootstrap.DRAIN.total_seconds() >= 60
