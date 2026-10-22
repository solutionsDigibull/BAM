"""E11.3 against the Compose Postgres: the code_bundles migration up and down, the new statuses,
the `artifacts` index, and storage running out of space (T07). A scratch database is created and
dropped. Needs `make up`; not part of the unit run."""

import asyncio
import errno
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from codegen_cases import Counter, ms01_bodies, ms01_loop_spec, ms01_params
from sbam_api.artifacts.models import Artifact, ArtifactKind
from sbam_api.artifacts.repository import ArtifactRepository
from sbam_api.builds.models import CodeAttemptStatus
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.storage.base import StorageUnavailable, StoredObject
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build import BuildStageInput
from sbam_workers.build import assemble as assemble_module
from sbam_workers.build.assemble import Assemble, AssembleInputs, SqlBundleStore
from sbam_workers.build.codegen import load_template, render_skeleton
from sqlalchemy import pool, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from temporalio.testing import ActivityEnvironment

from .test_db import url

pytestmark = pytest.mark.integration

REVISION = "0020_code_bundles"
DB = f"sbam_e113_{uuid.uuid4().hex[:10]}"
TENANT, OTHER, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    previous = ScriptDirectory.from_config(config()).get_revision(REVISION).down_revision
    assert isinstance(previous, str)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield make_sessionmaker(engine)
            with engine.begin() as conn:  # an assembled row must not block the downgrade
                conn.execute(text("UPDATE sbam.code_attempts SET status = 'ASSEMBLED'"))
            command.downgrade(config(), previous)  # this revision undoes itself
            with engine.connect() as conn:
                assert conn.execute(text("SELECT to_regclass('sbam.artifacts')")).scalar() is None
                statuses = conn.execute(text("SELECT DISTINCT status FROM sbam.code_attempts"))
                assert {s for (s,) in statuses} <= {"BODIES_GENERATED", "PRECHECK_FAILED"}
            command.upgrade(config(), "head")  # and up again
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def ctx(tenant: uuid.UUID = TENANT) -> TenantContext:
    return TenantContext(tenant, PROJECT)


def stored(**over: Any) -> StoredObject:
    sha = uuid.uuid4().hex * 2
    values: dict[str, Any] = {
        "storage_key": f"{TENANT}/{PROJECT}/{uuid.uuid4()}/code/{sha}",
        "sha256": sha,
        "size": 10240,
        "content_type": "application/x-tar",
    }
    return StoredObject(**{**values, **over})


def test_e11_3_artifact_index_is_once_per_key(sessions: sessionmaker[Session]) -> None:
    obj = stored()
    with sessions() as session:
        first = ArtifactRepository(session, ctx()).index(ArtifactKind.CODE_BUNDLE, obj, "test")
        session.commit()
    with sessions() as session:
        again = ArtifactRepository(session, ctx()).index(ArtifactKind.CODE_BUNDLE, obj, "test")
        assert again.id == first.id and again.is_frozen is False
        assert ArtifactRepository(session, ctx(OTHER)).find(obj.storage_key) is None
    with sessions() as session, pytest.raises(IntegrityError):  # the database enforces it too
        ArtifactRepository(session, ctx()).add(
            Artifact(
                kind="CODE_BUNDLE",
                storage_key=obj.storage_key,
                sha256=obj.sha256,
                size=1,
                content_type="application/x-tar",
            )
        )


def test_e11_3_artifact_kind_is_checked(sessions: sessionmaker[Session]) -> None:
    obj = stored()
    with sessions() as session, pytest.raises(IntegrityError):
        ArtifactRepository(session, ctx()).add(
            Artifact(
                kind="ANYTHING",
                storage_key=obj.storage_key,
                sha256=obj.sha256,
                size=1,
                content_type="application/x-tar",
            )
        )


# --- T07: the activity against Postgres and a full local disk -------------------------------------


def test_e11_3_t07_disk_full_is_retryable_and_leaves_no_trace(
    sessions: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = load_template("fastmcp-loopy", "1.0.0")
    params = ms01_params()
    inp = BuildStageInput(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=uuid.uuid4(),
        correlation_id="corr-t07",
        workflow_id=uuid.uuid4(),
        sub_skill_id=uuid.uuid4(),
        build_no=1,
    )
    with sessions() as session:
        CodeAttemptRepository(session, ctx()).record(
            {
                "workflow_id": inp.workflow_id,
                "sub_skill_id": inp.sub_skill_id,
                "manifest_id": None,
                "build_no": 1,
                "cycle": 0,
                "attempt": 0,
                "template_name": template.name,
                "template_version": template.version,
                "template_sha256": template.sha256,
                "status": CodeAttemptStatus.BODIES_GENERATED,
                "bodies": ms01_bodies(),
                "llm_call_ids": [],
                "trace_id": "trace-t07",
                "findings": [],
                "created_by": "test",
            }
        )
        session.commit()

    async def inputs(_: BuildStageInput) -> AssembleInputs:
        return AssembleInputs(template, params, render_skeleton(template, params), ms01_loop_spec())

    def no_space(_: int) -> None:
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    counter = Counter()
    monkeypatch.setattr(assemble_module, "_storage_failures", counter)
    storage = LocalDiskBackend(tmp_path)
    monkeypatch.setattr("sbam_api.storage.local.os.fsync", no_space)
    impl = Assemble(inputs, storage, SqlBundleStore(sessions))
    with pytest.raises(StorageUnavailable):  # retryable for Temporal
        asyncio.run(ActivityEnvironment().run(impl.run, inp))
    assert counter.value == 1
    with sessions() as session:
        row = CodeAttemptRepository(session, ctx()).find(inp.sub_skill_id, 1, 0, 0)
        assert row is not None
        assert row.status == CodeAttemptStatus.BODIES_GENERATED and row.bundle_key is None
        keys = [a.storage_key for a in ArtifactRepository(session, ctx()).list()]
        assert not any(str(inp.workflow_id) in key for key in keys)
    assert not any(p.is_file() for p in tmp_path.rglob("*"))  # no partial upload left behind

    monkeypatch.undo()  # space again: the retry assembles
    result = asyncio.run(ActivityEnvironment().run(impl.run, inp))
    with sessions() as session:
        row = CodeAttemptRepository(session, ctx()).get(result.id)
        assert row is not None and row.status == CodeAttemptStatus.ASSEMBLED
        assert row.files is not None and len(row.files) == 7
