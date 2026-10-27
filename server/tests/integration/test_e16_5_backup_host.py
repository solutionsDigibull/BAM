"""E16.5-T07 for real: a VM's login on the append-only rest-server can back up and read, never
delete. Runs the pinned restic and rest-server images with Docker; skips without Docker.
"""

import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[3]
DOCKER = shutil.which("docker")
HTPASSWD_IMAGE = (
    "httpd:2.4-alpine@sha256:070da7661f0be8a05430b2c125c0cde9e53176b4048c813024021969a0e3893c"
)


def pinned(path: str, name: str) -> str:
    found = re.search(rf"{name}:\d+\.\d+\.\d+@sha256:[0-9a-f]{{64}}", (REPO / path).read_text())
    assert found, name
    return found.group(0)


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv
        [DOCKER or "docker", *args], capture_output=True, text=True, check=check, timeout=180
    )


@pytest.mark.skipif(DOCKER is None, reason="needs Docker")
def test_e16_5_t07_vm_credentials_cannot_delete_backups() -> None:
    rest = pinned("compose/backup/compose.backup-host.yaml", "restic/rest-server")
    restic = pinned("scripts/backup.sh", "restic/restic")
    run = uuid.uuid4().hex[:8]
    net, server, data = f"sbam-bk-{run}", f"sbam-bk-rest-{run}", f"sbam-bk-data-{run}"
    htpasswd = docker("run", "--rm", HTPASSWD_IMAGE, "htpasswd", "-nbB", "sbam-test", "pw-1").stdout
    docker("network", "create", net)
    try:
        docker("volume", "create", data)
        # the htpasswd file goes into the data volume's config dir through a throwaway container
        docker(
            "run", "--rm", "-v", f"{data}:/data", "--entrypoint", "sh", rest, "-c",
            f"mkdir -p /data/.cfg && printf '%s' '{htpasswd.strip()}' > /data/.cfg/htpasswd",
        )  # fmt: skip
        docker(
            "run", "-d", "--name", server, "--network", net, "-v", f"{data}:/data", rest,
            "rest-server", "--path=/data", "--listen=:8000", "--append-only", "--private-repos",
            "--htpasswd-file=/data/.cfg/htpasswd",
        )  # fmt: skip

        def vm(*args: str) -> subprocess.CompletedProcess[str]:
            return docker(
                "run", "--rm", "--network", net,
                "-e", f"RESTIC_REPOSITORY=rest:http://{server}:8000/sbam-test/",
                "-e", "RESTIC_PASSWORD=repo-key", "-e", "RESTIC_REST_USERNAME=sbam-test",
                "-e", "RESTIC_REST_PASSWORD=pw-1", "-e", "RESTIC_HOST=sbam-test",
                restic, *args, check=False,
            )  # fmt: skip

        for _ in range(30):  # rest-server starting
            if vm("init").returncode == 0:
                break
        assert vm("backup", "--tag", "set:1", "/etc/hostname").returncode == 0
        assert vm("snapshots", "--json").stdout.count('"id"') == 1

        forget = vm("forget", "--prune", "latest")
        assert forget.returncode != 0 and "403" in forget.stdout + forget.stderr
        assert vm("snapshots", "--json").stdout.count('"id"') == 1  # still there
        assert vm("dump", "latest", "/etc/hostname").returncode == 0  # and readable for a restore
    finally:
        docker("rm", "-f", server, check=False)
        docker("volume", "rm", "-f", data, check=False)
        docker("network", "rm", net, check=False)
