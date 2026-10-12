"""E1.6-T08 disk full: the real sbam-api image writing to a 256 KB tmpfs (TD §19.3).

Runs the installed `sbam_api` (not the host checkout) with STORAGE_ROOT on a tiny tmpfs, puts 1 MB,
and expects StorageUnavailable with no partial file anywhere under the root.
"""

import subprocess

import pytest

pytestmark = pytest.mark.integration

SNIPPET = """
import pathlib, uuid
from sbam_api.storage import Kind, StorageScope, StorageUnavailable
from sbam_api.storage.local import LocalDiskBackend
root = pathlib.Path("/s")
store = LocalDiskBackend(root)
try:
    store.put(b"x" * (1024 * 1024), StorageScope(uuid.uuid4(), uuid.uuid4()), Kind.UPLOADS, "x")
    print("STORED")
except StorageUnavailable as exc:
    print("UNAVAILABLE", exc.status)
print("FILES", sum(1 for p in root.rglob("*") if p.is_file()))
"""


def test_e1_6_t08_disk_full_is_unavailable_with_no_partial_file() -> None:
    result = subprocess.run(  # noqa: S603 - fixed docker argv
        [  # noqa: S607
            "docker",
            "run",
            "--rm",
            "--tmpfs",
            "/s:size=256k,uid=10001,gid=10001",
            "--entrypoint",
            "python",
            "sbam-api:local",
            "-c",
            SNIPPET,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "UNAVAILABLE 503" in result.stdout
    assert "FILES 0" in result.stdout
