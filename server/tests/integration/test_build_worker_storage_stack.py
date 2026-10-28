"""#404: `worker-build` has storage.

It runs the SOW export (`SowExport`, E6.4), whose `export_sow` activity builds a `StorageBackend`.
Without `STORAGE_ROOT` and the storage volume every export failed with `STORAGE_BACKEND=local
needs STORAGE_ROOT` and the API answered 503 EXPORT_TIMEOUT. No model is involved."""

import uuid

import pytest

from .conftest import Compose

pytestmark = pytest.mark.integration


def worker(compose: Compose, *command: str) -> tuple[int, str]:
    run = compose("exec", "-T", "worker-build", *command)
    if "is not running" in run.stderr or "No such service" in run.stderr:
        pytest.skip("worker-build is not running")
    return run.returncode, run.stdout + run.stderr


def test_e6_404_worker_build_has_the_storage_root(compose: Compose) -> None:
    code, out = worker(compose, "printenv", "STORAGE_ROOT")
    assert code == 0 and out.strip() == "/data/storage", out


def test_e6_404_worker_build_can_write_to_the_storage_volume(compose: Compose) -> None:
    probe = f"/data/storage/.tmp/e6-404-{uuid.uuid4().hex}"
    code, out = worker(compose, "sh", "-c", f"touch {probe} && rm {probe}")
    assert code == 0, out[-500:]


def test_e6_404_worker_build_builds_the_storage_backend(compose: Compose) -> None:
    """What export_sow does first: `make_backend(settings)` (it raised ValueError before)."""
    code, out = worker(
        compose,
        "python",
        "-c",
        "from sbam_workers.settings import WorkerSettings; "
        "from sbam_api.storage.factory import make_backend; "
        "print(type(make_backend(WorkerSettings())).__name__)",
    )
    assert code == 0 and "LocalDiskBackend" in out, out[-500:]
