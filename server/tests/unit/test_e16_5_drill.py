"""E16.5: scripts/restore-drill.sh against a fake `docker` (restic, compose, pg_restore, the hash
verifier) and a fake deploy.sh in the bundle."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DRILL = REPO / "scripts" / "restore-drill.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt", reason="bash on Linux (CI)")
FILES = [f"{d}.dump" for d in ("sbam", "temporal", "temporal_visibility", "keycloak", "langfuse")]
FILES += ["storage.tar", "cognee_data.tar"]
SET_ID = "20261026T023000Z"
OK_VERIFY = '{"checked": 3, "matched": 3, "mismatched": [], "missing": [], "ok": true}'

# `restore` copies $FAKE_DIR/set into the mounted /restore; `exec … verify` prints FAKE_VERIFY and
# fails when it says "ok": false; FAKE_VOLUMES makes `volume ls` find existing data.
FAKE_DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_DIR/calls"
[[ $1 == volume && $2 == ls ]] && { [[ -n ${FAKE_VOLUMES:-} ]] && echo sbam_pgdata; exit 0; }
case " $* " in
  *" restore "*)
    for a in "$@"; do [[ $a == *:/restore ]] && target="${a%:/restore}"; done
    mkdir -p "$target/data" && cp -R "$FAKE_DIR/set" "$target/data/$SET_ID"; exit 0 ;;
  *" pg_restore "*) cat >/dev/null; [[ $* == *" -d ${FAKE_FAIL:-none} "* ]] && exit 1; exit 0 ;;
  *" tar -C /v -xf "*) cat >/dev/null; exit 0 ;;
  *"sbam_api.artifacts.verify"*) echo "${FAKE_VERIFY}"; [[ $FAKE_VERIFY == *'"ok": true'* ]] ;;
esac
"""


@pytest.fixture
def prod_port() -> Iterator[int]:
    """A listening socket standing in for a production endpoint."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        yield server.getsockname()[1]


class DrillVm:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.fake = tmp / "fake"
        (self.fake / "set").mkdir(parents=True)
        (self.fake / "docker").write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
        (self.fake / "docker").chmod(0o755)
        bundle = tmp / "bundle"
        (bundle / "compose").mkdir(parents=True)
        (bundle / "compose" / "compose.yaml").write_text("name: sbam\n")
        (bundle / "scripts").mkdir()
        (bundle / "scripts" / "deploy.sh").write_text(
            'echo "deploy $*" >>"$FAKE_DIR/calls"; exit "${FAKE_DEPLOY_RC:-0}"\n'
        )
        self.secrets = tmp / "secrets"
        self.secrets.mkdir()
        for name in ("backup_repo_password", "backup_rest_password"):
            (self.secrets / name).write_text("x")
        for name in ("drill.env", "release.env"):
            (tmp / name).write_text("X=1\n")
        self.backup_set(datetime.now(UTC) - timedelta(hours=3))

    def backup_set(self, finished: datetime, corrupt: str = "") -> None:
        entries = {}
        for name in FILES:
            data = f"content of {name}".encode()
            (self.fake / "set" / name).write_bytes(b"corrupted" if name == corrupt else data)
            entries[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        stamp = finished.strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest = {"set": SET_ID, "env": "prod", "started": stamp, "finished": stamp}
        (self.fake / "set" / "manifest.json").write_text(json.dumps({**manifest, "files": entries}))

    def drill(self, *extra: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, test-owned paths
            [
                *(BASH or "bash", str(DRILL), "--env-file", str(self.tmp / "drill.env")),
                *("--release", str(self.tmp / "release.env"), "--bundle", str(self.tmp / "bundle")),
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "PATH": f"{self.fake}{os.pathsep}{os.environ['PATH']}",
                "FAKE_DIR": str(self.fake),
                "SET_ID": SET_ID,
                "FAKE_VERIFY": OK_VERIFY,
                "BACKUP_HOST": "backup.netbird:8000",
                "BACKUP_USER": "sbam-prod",
                "SBAM_SECRETS_DIR": str(self.secrets),
                "DRILL_DIR": str(self.tmp / "drill"),
                "DRILL_FORBIDDEN_HOSTS": "127.0.0.1:9",  # nothing listens on port 9
                **env,
            },
        )

    def calls(self) -> list[str]:
        path = self.fake / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def report(self) -> dict[str, object]:
        (path,) = (self.tmp / "drill").glob("drill-*.json")
        result: dict[str, object] = json.loads(path.read_text())
        return result


@pytest.fixture
def vm(tmp_path: Path) -> DrillVm:
    return DrillVm(tmp_path)


def test_e16_5_t02_drill_restores_starts_verifies_and_reports(vm: DrillVm) -> None:
    out = vm.drill()

    assert out.returncode == 0, out.stderr
    calls = vm.calls()
    steps = [
        next(i for i, c in enumerate(calls) if needle in c)
        for needle in (
            " restore ",
            "up -d --wait postgres",
            " create",
            "tar -C /v -xf",
            "pg_restore",
        )
    ]
    assert steps == sorted(steps)  # restore set -> postgres -> volumes -> databases
    assert sum("pg_restore" in c for c in calls) == 5 and sum("-xf" in c for c in calls) == 2
    deploy = next(c for c in calls if c.startswith("deploy "))
    assert f"--id drill-{SET_ID}" in deploy and "--env dev" in deploy  # the mock-LLM overlay
    assert sum("sbam_api.artifacts.verify" in c for c in calls) == 2  # frozen, then --all
    report = vm.report()
    assert report["result"] == "PASS" and report["set"] == SET_ID
    assert report["frozen"] == json.loads(OK_VERIFY)


def test_e16_5_t03_one_frozen_mismatch_fails_the_drill(vm: DrillVm) -> None:
    bad = '{"checked": 3, "matched": 2, "mismatched": ["t/p/code/abc"], "missing": [], "ok": false}'
    out = vm.drill(FAKE_VERIFY=bad)

    assert out.returncode == 1
    assert vm.report()["result"] == "FAIL"


def test_e16_5_t04_rpo_is_measured_against_the_failure_time(vm: DrillVm) -> None:
    vm.backup_set(datetime(2026, 10, 26, 2, 30, tzinfo=UTC))

    ok = vm.drill("--failure-at", "2026-10-26T20:30:00Z")
    assert ok.returncode == 0 and vm.report()["rpo_seconds"] == 18 * 3600

    for old in (vm.tmp / "drill").glob("drill-*.json"):
        old.unlink()
    late = vm.drill("--failure-at", "2026-10-27T04:30:00Z")  # 26 h after the last good set
    report = vm.report()
    assert late.returncode == 1 and report["rpo_ok"] is False and report["result"] == "FAIL"


def test_e16_5_drill_refuses_a_vm_that_reaches_production(vm: DrillVm, prod_port: int) -> None:
    out = vm.drill(DRILL_FORBIDDEN_HOSTS=f"127.0.0.1:9 127.0.0.1:{prod_port}")

    assert out.returncode == 1 and "must be isolated from production" in out.stderr
    assert not any(" restore " in c for c in vm.calls())


def test_e16_5_drill_never_restores_over_existing_data(vm: DrillVm) -> None:
    out = vm.drill(FAKE_VOLUMES="1")

    assert out.returncode == 1 and "only runs on a clean VM" in out.stderr
    assert [c for c in vm.calls() if not c.startswith("volume ls")] == []


def test_e16_5_a_restored_file_that_does_not_match_its_manifest_stops_the_drill(
    vm: DrillVm,
) -> None:
    vm.backup_set(datetime.now(UTC), corrupt="keycloak.dump")

    out = vm.drill()

    assert out.returncode == 1 and "keycloak.dump does not match the manifest" in out.stderr
    assert not any("postgres" in c for c in vm.calls())


def test_e16_5_a_failed_pg_restore_stops_the_drill(vm: DrillVm) -> None:
    out = vm.drill(FAKE_FAIL="keycloak")

    assert out.returncode == 1 and "pg_restore keycloak" in out.stderr
    assert not any(c.startswith("deploy ") for c in vm.calls())
