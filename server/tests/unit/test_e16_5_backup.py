"""E16.5: scripts/backup.sh and backup-prune.sh against a fake `docker` (dumps, tars and a restic
repository played by files), plus the backup host's append-only configuration."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BACKUP = REPO / "scripts" / "backup.sh"
PRUNE = REPO / "scripts" / "backup-prune.sh"
BASH = shutil.which("bash")
DATABASES = ["sbam", "temporal", "temporal_visibility", "keycloak", "langfuse"]
VOLUMES = ["storage", "cognee_data"]

# `docker ps` finds postgres; `exec … pg_dump <db>` and `run … tar` print fake contents (FAKE_FAIL
# makes one fail); the restic container's `backup` copies the mounted work dir into $FAKE_DIR/repo
# and `dump … /data/<set>/<file>` reads it back (FAKE_CORRUPT alters one file on the way).
FAKE_DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$FAKE_DIR/calls"
case "$1" in
  ps) [[ -z ${FAKE_NO_PG:-} ]] && echo pgcid; exit 0 ;;
  exec) db="${@: -1}"; [[ $db == "${FAKE_FAIL:-}" ]] && exit 1; echo "dump of $db"; exit 0 ;;
esac
if [[ $* == *" tar -C /v "* ]]; then
  vol=""; for a in "$@"; do [[ $a == *:/v:ro ]] && vol="${a%%:/v:ro}"; done
  [[ $vol == *"${FAKE_FAIL:-none}" ]] && exit 1; echo "tar of $vol"; exit 0
fi
work=""; for a in "$@"; do [[ $a == *:/data:ro ]] && work="${a%:/data:ro}"; done
case " $* " in
  *" backup "*)
    mkdir -p "$FAKE_DIR/repo" && cp -R "$work/." "$FAKE_DIR/repo/"; exit "${FAKE_BACKUP_RC:-0}" ;;
  *" dump "*)
    path="${@: -1}"; file="$FAKE_DIR/repo/${path#/data/}"
    if [[ "$(basename "$path")" == "${FAKE_CORRUPT:-none}" ]]; then echo corrupted
    else cat "$file"; fi ;;
  *" forget "*) exit "${FAKE_FORGET_RC:-0}" ;;
esac
"""
FAKE_CURL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$FAKE_DIR/alerts"
"""

needs_bash = pytest.mark.skipif(BASH is None or os.name == "nt", reason="bash on Linux (CI)")


class Vm:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.fake = tmp / "fake"
        self.fake.mkdir()
        for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
            (self.fake / name).write_text(body, encoding="utf-8", newline="\n")
            (self.fake / name).chmod(0o755)
        self.secrets = tmp / "secrets"
        self.secrets.mkdir()
        (self.secrets / "backup_repo_password").write_text("repo-key\n")
        (self.secrets / "backup_rest_password").write_text("rest-pw")
        self.state = tmp / "state"

    def run(self, script: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, test-owned paths
            [BASH or "bash", str(script), *args],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "PATH": f"{self.fake}{os.pathsep}{os.environ['PATH']}",
                "FAKE_DIR": str(self.fake),
                "BACKUP_HOST": "backup.netbird:8000",
                "BACKUP_USER": "sbam-staging",
                "BACKUP_STATE": str(self.state),
                "SBAM_SECRETS_DIR": str(self.secrets),
                "SBAM_ALERT_WEBHOOK_URL": "http://hook/ops",
                **env,
            },
        )

    def backup(self, **env: str) -> subprocess.CompletedProcess[str]:
        return self.run(BACKUP, "--env", "staging", **env)

    def calls(self) -> list[str]:
        path = self.fake / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def log(self) -> list[dict[str, str]]:
        return [json.loads(line) for line in (self.state / "log.jsonl").read_text().splitlines()]

    def alerts(self) -> str:
        path = self.fake / "alerts"
        return path.read_text() if path.exists() else ""


@pytest.fixture
def vm(tmp_path: Path) -> Vm:
    return Vm(tmp_path)


@needs_bash
def test_e16_5_t01_nightly_set_is_dumped_uploaded_and_verified(vm: Vm) -> None:
    out = vm.backup()

    assert out.returncode == 0, out.stderr
    dumps = [c.split()[-1] for c in vm.calls() if c.startswith("exec ")]
    assert dumps == DATABASES
    assert sum(" tar -C /v " in c for c in vm.calls()) == len(VOLUMES)
    (set_dir,) = (vm.fake / "repo").iterdir()
    manifest = json.loads((set_dir / "manifest.json").read_text())
    names = [f"{d}.dump" for d in DATABASES] + [f"{v}.tar" for v in VOLUMES]
    assert sorted(manifest["files"]) == sorted(names) and manifest["env"] == "staging"
    for name, entry in manifest["files"].items():
        data = (set_dir / name).read_bytes()
        assert entry == {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    # every file (and the manifest) is read back from the repository after the upload
    assert sum(" dump " in f" {c} " for c in vm.calls()) == len(names) + 1
    assert vm.log()[-1]["result"] == "OK" and (vm.state / "last_success").exists()
    assert not (vm.state / "work").exists() and vm.alerts() == ""


@needs_bash
def test_e16_5_t01_a_file_that_does_not_read_back_fails_the_set(vm: Vm) -> None:
    out = vm.backup(FAKE_CORRUPT="keycloak.dump")

    assert out.returncode == 1 and "keycloak.dump in the repository does not match" in out.stderr
    assert vm.log()[-1]["result"] == "FAILED" and "FAILED" in vm.alerts()
    assert not (vm.state / "last_success").exists()


@needs_bash
@pytest.mark.parametrize("broken", ["keycloak", "cognee_data"])
def test_e16_5_t05_one_failed_dump_fails_the_whole_set(vm: Vm, broken: str) -> None:
    assert vm.backup().returncode == 0  # an earlier good set
    before = (vm.state / "last_success").read_text()
    uploads = sum(" backup " in f" {c} " for c in vm.calls())

    out = vm.backup(FAKE_FAIL=broken)

    assert out.returncode == 1 and "previous sets are untouched" in out.stderr
    assert sum(" backup " in f" {c} " for c in vm.calls()) == uploads  # nothing uploaded
    record = vm.log()[-1]
    assert record["result"] == "FAILED" and broken in record["reason"]
    assert broken in vm.alerts() and (vm.state / "last_success").read_text() == before


@needs_bash
def test_e16_5_missing_postgres_or_secrets_fail_loudly(vm: Vm) -> None:
    assert vm.backup(FAKE_NO_PG="1").returncode == 1
    (vm.secrets / "backup_repo_password").write_text("")
    out = vm.backup()
    assert out.returncode == 1 and "backup_repo_password is missing or empty" in out.stderr


@needs_bash
def test_e16_5_t06_prune_keeps_thirty_days_and_always_the_newest_set(vm: Vm) -> None:
    out = vm.run(PRUNE, "sbam-dev", "sbam-staging", BACKUP_DATA_DIR="/srv/b")

    assert out.returncode == 0, out.stderr
    forgets = [c for c in vm.calls() if " forget " in f" {c} "]
    assert len(forgets) == 2
    for call in forgets:
        # per environment (host), not per path: every set has its own path
        assert "forget --group-by host --keep-within 30d --keep-last 1 --prune" in call
    assert vm.run(PRUNE, "../etc").returncode == 1  # repo names are plain names


def test_e16_5_t07_vms_can_add_backups_but_never_delete_them() -> None:
    host = yaml.safe_load((REPO / "compose/backup/compose.backup-host.yaml").read_text())
    command = host["services"]["rest-server"]["command"]
    assert "--append-only" in command and "--private-repos" in command and "--tls" in command
    assert any(c.startswith("--htpasswd-file=") for c in command)
    text = BACKUP.read_text(encoding="utf-8")
    assert not re.search(r"^\s*restic (forget|prune)\b", text, re.MULTILINE)
    assert re.search(r"restic/restic:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", text)
    assert re.search(r"rest-server:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", json.dumps(host))


def test_e16_5_timer_runs_nightly_and_catches_up() -> None:
    timer = (REPO / "compose/backup/sbam-backup.timer").read_text()
    assert "OnCalendar=*-*-* 02:30:00 UTC" in timer and "Persistent=true" in timer
    service = (REPO / "compose/backup/sbam-backup.service").read_text()
    assert "ExecStart=/opt/sbam/bin/backup.sh --env ${BACKUP_ENV}" in service
