"""Test helpers for E12.1: SQLite with the runtime registry tables, and sample registries."""

import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any

from sbam_api.registry.models import RuntimeRegistryPackageRow, RuntimeRegistryRow
from sbam_contracts.runtime_registry import RuntimeRegistry
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

REPO = Path(__file__).resolve().parents[2]


def sqlite_sessions() -> sessionmaker[Session]:
    fd, path = tempfile.mkstemp(prefix="sbam-registry-", suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"sbam": None}},
    )
    tables = [RuntimeRegistryRow.__table__, RuntimeRegistryPackageRow.__table__]
    RuntimeRegistryRow.metadata.create_all(engine, tables=tables)  # type: ignore[arg-type]
    return sessionmaker(engine, expire_on_commit=False)


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "skill_runtime_registry", REPO / "compose/docker/skill-runtime/registry.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = _generator()
ALLOWED_IN = str(REPO / "compose/docker/skill-runtime/requirements.in")


def sample_registry(digest_byte: str = "ab", **overrides: Any) -> RuntimeRegistry:
    """A real registry (from this Python environment) for image digest sha256:<digest_byte * 32>."""
    raw = GENERATOR.build(
        "sha256:" + digest_byte * 32, "2026-10-13T00:00:00+00:00", allowed_file=ALLOWED_IN
    )
    raw.update(overrides)
    if overrides:
        raw["content_sha256"] = GENERATOR.content_sha256(raw)
    return RuntimeRegistry.model_validate(raw)
