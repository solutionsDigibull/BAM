"""E1.1 scaffold checks: workspace packages import and the repo layout matches TD §6."""

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

REQUIRED_DIRS = [
    "client",
    "client/src/app",
    "server/api/sbam_api",
    "server/workers/sbam_workers",
    "server/contracts/sbam_contracts",
    "server/templates",
    "server/tests/unit",
    "server/tests/api",
    "server/tests/contract",
    "server/tests/temporal",
    "server/tests/fixtures",
    "compose",
    "docs",
]
FORBIDDEN_DIRS = ["apps", "services", "packages", "deploy"]


@pytest.mark.parametrize("name", ["sbam_api", "sbam_workers", "sbam_contracts"])
def test_e1_1_t03_packages_import(name: str) -> None:
    assert importlib.import_module(name).__doc__


def test_e1_1_t04_layout_conformance() -> None:
    missing = [d for d in REQUIRED_DIRS if not (REPO_ROOT / d).is_dir()]
    forbidden = [d for d in FORBIDDEN_DIRS if (REPO_ROOT / d).exists()]
    assert not missing, f"missing TD §6 dirs: {missing}"
    assert not forbidden, f"non-TD §6 top-level dirs: {forbidden}"
    assert (REPO_ROOT / "Makefile").is_file()
