"""E12.1 against the Compose Postgres (and, for T05, the built skill-runtime image).

The registry tests use a scratch database migrated to head, so the shared `sbam` database is never
touched. T05 needs the image: set SKILL_RUNTIME_IMAGE (CI's compose-smoke builds it); else skipped.
"""

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from registry_db import sample_registry
from sbam_api.core.auth import ROLE_FDE, CurrentUser, fde
from sbam_api.core.settings import load_settings
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.main import create_app
from sbam_api.registry.repository import active_registry, registry_digests
from sbam_api.registry.sync import main as sync_main
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e121_{uuid.uuid4().hex[:10]}"


@pytest.fixture(scope="module")
def scratch_url() -> Iterator[str]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    scratch = url(database=DB)
    previous = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = scratch
        command.upgrade(config(), "head")
        yield scratch
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def sessions(scratch_url: str) -> sessionmaker[Session]:
    return make_sessionmaker(make_engine(scratch_url))


def test_e12_1_t01_sync_cli_then_api_returns_the_registry(
    scratch_url: str, sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    registry = sample_registry("e1")
    file = tmp_path / "runtime_registry.json"
    file.write_text(registry.model_dump_json(), encoding="utf-8")
    assert sync_main([str(file), "--activate", "--database-url", scratch_url]) == 0
    assert sync_main([str(file), "--activate", "--database-url", scratch_url]) == 0  # no-op
    with sessions() as session:
        assert registry_digests(session) == [(registry.image_digest, True)]
        assert active_registry(session) == registry  # JSONB modules + timestamps round trip

    settings = load_settings(
        _env_file=None,
        database_url=scratch_url,
        storage_root=tmp_path,
        temporal_host="127.0.0.1:1",
        keycloak_issuer="http://127.0.0.1:1/realms/sbam",
        keycloak_audience="sbam-api",
    )
    app = create_app(settings, {})
    app.dependency_overrides[fde] = lambda: CurrentUser(
        user_id=uuid.uuid4(), keycloak_sub="s", tenant_id=uuid.uuid4(), roles=frozenset({ROLE_FDE})
    )
    body = TestClient(app).get("/api/v1/registry").json()
    assert body["image_digest"] == registry.image_digest
    assert len(body["packages"]) == len(registry.packages)


def test_e12_1_t06_db_down_exit_1_and_nothing_written(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    registry = sample_registry("e2")
    file = tmp_path / "runtime_registry.json"
    file.write_text(registry.model_dump_json(), encoding="utf-8")
    dead = "postgresql://sbam:x@127.0.0.1:1/sbam"  # nothing listens on port 1
    assert sync_main([str(file), "--activate", "--database-url", dead, "--attempts", "2"]) == 1
    with sessions() as session:
        assert registry.image_digest not in {d for d, _ in registry_digests(session)}


@pytest.mark.skipif(
    not os.environ.get("SKILL_RUNTIME_IMAGE") or not shutil.which("docker"),
    reason="set SKILL_RUNTIME_IMAGE to a built skill-runtime image",
)
def test_e12_1_t05_postgres_in_image_ready_under_10s() -> None:
    image = os.environ["SKILL_RUNTIME_IMAGE"]
    docker = shutil.which("docker") or "docker"
    script = (
        "s=$(date +%s%N); sbam-pg-start >/dev/null && "
        'psql -h 127.0.0.1 -U postgres -Atc "show listen_addresses" && '
        'echo "ms=$(( ($(date +%s%N) - s) / 1000000 ))"'
    )
    started = time.monotonic()
    out = subprocess.run(  # noqa: S603 - fixed docker argv
        [docker, "run", "--rm", "--network", "none", "--entrypoint", "sh", image, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout.split()
    assert out[0] == "127.0.0.1"  # localhost only
    ready_ms = int(out[1].removeprefix("ms="))
    assert ready_ms < 10_000, f"postgres ready after {ready_ms} ms"
    assert time.monotonic() - started < 60
