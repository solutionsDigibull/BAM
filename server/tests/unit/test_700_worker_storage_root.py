"""#700: a worker that opens `StorageBackend` has `STORAGE_ROOT` and the storage volume in Compose.

`worker-test` had neither, so `run_sandbox_tests` failed with `STORAGE_BACKEND=local needs
STORAGE_ROOT` and every Sub-Skill that reached the sandbox ended in TESTING_BLOCKED."""

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "compose" / "compose.yaml"

# Every worker whose activities open `StorageBackend`. A new storage worker is added here.
STORAGE_WORKERS = [
    "worker-ingest",
    "worker-knowledge",
    "worker-llm",
    "worker-build",
    "worker-test",
    "worker-certify",
]


class ComposeLoader(yaml.SafeLoader):
    """Compose's own tags read as the value they wrap."""


ComposeLoader.add_constructor("!reset", lambda loader, node: None)
ComposeLoader.add_constructor("!override", lambda loader, node: None)


def services() -> dict[str, dict[str, object]]:
    # ComposeLoader is a SafeLoader that only adds Compose's own tags
    document = yaml.load(COMPOSE.read_text(encoding="utf-8"), Loader=ComposeLoader)  # noqa: S506
    result: dict[str, dict[str, object]] = document["services"]
    return result


@pytest.mark.parametrize("service", STORAGE_WORKERS)
def test_700_a_storage_worker_has_the_storage_root(service: str) -> None:
    environment = services()[service]["environment"]
    assert isinstance(environment, dict)
    assert environment["STORAGE_ROOT"] == "/data/storage"


@pytest.mark.parametrize("service", STORAGE_WORKERS)
def test_700_a_storage_worker_mounts_the_storage_volume(service: str) -> None:
    volumes = services()[service]["volumes"]
    assert isinstance(volumes, list)
    assert "storage:/data/storage" in volumes


def test_700_the_test_worker_keeps_its_promptfoo_jobs_volume() -> None:
    volumes = services()["worker-test"]["volumes"]
    assert isinstance(volumes, list)
    assert "promptfoo-jobs:/jobs" in volumes
