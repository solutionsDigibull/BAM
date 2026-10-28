"""#373: `worker-llm` has storage.

It runs generate_sow, the SOW export and the ISATVON macros, which all build a `StorageBackend`.
Without `STORAGE_ROOT` and the storage volume the SOW stage failed with `STORAGE_BACKEND=local
needs STORAGE_ROOT` and every project ended in SOW_GENERATION_FAILED. No model is involved."""

import uuid

import pytest

from .conftest import Compose

pytestmark = pytest.mark.integration


def worker(compose: Compose, *command: str) -> tuple[int, str]:
    run = compose("exec", "-T", "worker-llm", *command)
    if "is not running" in run.stderr or "No such service" in run.stderr:
        pytest.skip("worker-llm is not running")
    return run.returncode, run.stdout + run.stderr


def test_e6_373_worker_llm_has_the_storage_root(compose: Compose) -> None:
    code, out = worker(compose, "printenv", "STORAGE_ROOT")
    assert code == 0 and out.strip() == "/data/storage", out


def test_e6_373_worker_llm_can_write_to_the_storage_volume(compose: Compose) -> None:
    probe = f"/data/storage/.tmp/e6-373-{uuid.uuid4().hex}"
    code, out = worker(compose, "sh", "-c", f"touch {probe} && rm {probe}")
    assert code == 0, out[-500:]


def test_e6_373_worker_llm_builds_the_storage_backend(compose: Compose) -> None:
    """What generate_sow does first: `make_backend(settings)` (it raised ValueError before)."""
    code, out = worker(
        compose,
        "python",
        "-c",
        "from sbam_workers.settings import WorkerSettings; "
        "from sbam_api.storage.factory import make_backend; "
        "print(type(make_backend(WorkerSettings())).__name__)",
    )
    assert code == 0 and "LocalDiskBackend" in out, out[-500:]
