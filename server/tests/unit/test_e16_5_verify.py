"""E16.5-T03: `python -m sbam_api.artifacts.verify` re-hashes stored artifacts after a restore."""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from llm_db import llm_sessions
from sbam_api.artifacts import verify as verify_module
from sbam_api.artifacts.models import Artifact, ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository, every_artifact
from sbam_api.db.repository import TenantContext
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sqlalchemy.orm import Session, sessionmaker

TENANTS = [uuid.uuid4(), uuid.uuid4()]
PROJECT = uuid.uuid4()


def tamper(path: Path, data: bytes) -> None:
    path.chmod(0o600)  # stored objects are read-only
    path.write_bytes(data)


@dataclass
class World:
    sessions: sessionmaker[Session]
    storage: LocalDiskBackend
    keys: list[str]  # tenant 0 frozen, tenant 0 not frozen, tenant 1 frozen, tenant 1 not frozen

    def report(self, *, frozen_only: bool) -> dict[str, Any]:
        with self.sessions() as session:
            return verify_module.verify(
                every_artifact(session, frozen_only=frozen_only), self.storage
            )


@pytest.fixture
def world(tmp_path: Path) -> World:
    sessions = llm_sessions()
    Artifact.metadata.create_all(sessions.kw["bind"], tables=[Artifact.__table__])  # type: ignore[list-item]
    storage = LocalDiskBackend(tmp_path / "storage")
    keys = []
    for n, tenant in enumerate(TENANTS):
        with sessions() as session:
            repo = ArtifactRepository(session, TenantContext(tenant, PROJECT))
            for i, frozen in enumerate((True, False)):
                data = f"bundle {n}.{i}".encode()
                stored = storage.put(data, StorageScope(tenant, PROJECT), Kind.CODE, "x/tar")
                repo.index(ArtifactKind.CODE_BUNDLE, stored, created_by="test").is_frozen = frozen
                keys.append(stored.storage_key)
            session.commit()
    return World(sessions, storage, keys)


def test_e16_5_t03_every_frozen_artifact_of_every_tenant_matches(world: World) -> None:
    assert world.report(frozen_only=True) == {
        "checked": 2,
        "matched": 2,
        "mismatched": [],
        "missing": [],
        "ok": True,
    }
    assert world.report(frozen_only=False)["checked"] == 4  # --all


def test_e16_5_t03_one_changed_or_missing_object_fails_the_check(world: World) -> None:
    tamper(world.storage.root / world.keys[0], b"tampered after the restore")
    (world.storage.root / world.keys[2]).chmod(0o600)
    (world.storage.root / world.keys[2]).unlink()

    result = world.report(frozen_only=True)

    assert result["ok"] is False and result["matched"] == 0
    assert result["mismatched"] == [world.keys[0]] and result["missing"] == [world.keys[2]]


def test_e16_5_cli_prints_the_report_and_exits_non_zero_on_a_mismatch(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verify_module, "load_settings", lambda: SimpleNamespace(database_url="x"))
    monkeypatch.setattr(verify_module, "make_engine", lambda url: None)
    monkeypatch.setattr(verify_module, "make_sessionmaker", lambda engine: world.sessions)
    monkeypatch.setattr(verify_module, "make_backend", lambda settings: world.storage)

    assert verify_module.main([]) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "frozen"

    tamper(world.storage.root / world.keys[1], b"x")  # a non-frozen one: only --all sees it
    assert verify_module.main([]) == 0
    capsys.readouterr()
    assert verify_module.main(["--all"]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["scope"] == "all" and printed["mismatched"] == [world.keys[1]]
