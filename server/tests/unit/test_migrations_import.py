"""Every migration imports outside a running migration (alembic heads, the drift test, tooling)."""

import importlib.util
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "api/sbam_api/migrations/versions"


@pytest.mark.parametrize("path", sorted(VERSIONS.glob("*.py")), ids=lambda p: p.stem)
def test_a_migration_imports_without_an_alembic_context(path: Path) -> None:
    """`op.f()` and other `op` calls at module level raise here: `op` only exists in `upgrade()`."""
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec and spec.loader
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
