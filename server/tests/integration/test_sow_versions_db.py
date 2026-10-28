"""E6.3 on the Compose Postgres: migration 0020 makes a SOW version's content immutable.

A scratch database migrated to head; dropped at the end.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from sbam_api.core.ids import uuid7
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.projects.models import Project
from sbam_api.sow.models import SowVersion
from sbam_api.sow.service import Generation, record_generation
from sbam_api.sow.sql import IMMUTABLE_SQLSTATE
from sbam_contracts.sow import SECTIONS, SowDocument
from sqlalchemy import pool, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e63_{uuid.uuid4().hex[:10]}"
TENANT = uuid.uuid4()
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"
DOC = SowDocument.model_validate_json(FIXTURE.read_text(encoding="utf-8")).model_dump(mode="json")


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture(scope="module")
def sessions(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        monkeypatch_module.setenv("DATABASE_URL", url(database=DB))
        command.upgrade(config(), "head")
        yield make_sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def a_version(sessions: sessionmaker[Session]) -> uuid.UUID:
    with sessions() as s:
        project = Project(tenant_id=TENANT, name=f"T08-{uuid.uuid4().hex[:6]}", status="ACTIVE")
        project.state = "SOW_REVIEW"
        s.add(project)
        s.flush()
        kv = KnowledgeVersion(
            id=uuid7(),
            tenant_id=TENANT,
            project_id=project.id,
            version=1,
            run_id=uuid7(),
            cognee_dataset="d",
            coverage={},
            state="READY",
        )
        s.add(kv)
        s.flush()
        ctx = TenantContext(TENANT, project.id)
        gen = Generation(uuid7(), kv.id, DOC, None)
        version = record_generation(s, ctx, gen, correlation_id="corr-e63-t08")
        s.commit()
        return version.id


def test_e6_3_t08_content_update_and_delete_are_refused_by_the_database(
    sessions: sessionmaker[Session],
) -> None:
    version_id = a_version(sessions)
    for statement in (
        "UPDATE sbam.sow_versions SET structured = '{}'::jsonb WHERE id = :id",
        "UPDATE sbam.sow_versions SET sha256 = 'x' WHERE id = :id",
        "UPDATE sbam.sow_versions SET version = 9 WHERE id = :id",
        "DELETE FROM sbam.sow_versions WHERE id = :id",
    ):
        with sessions() as s, pytest.raises(DBAPIError) as caught:
            s.execute(text(statement), {"id": version_id})
        assert getattr(caught.value.orig, "sqlstate", None) == IMMUTABLE_SQLSTATE
    with sessions() as s:
        row = s.execute(
            text("SELECT structured, version FROM sbam.sow_versions WHERE id = :id"),
            {"id": version_id},
        ).one()
    assert row.version == 1 and row.structured == DOC  # unchanged


def test_e6_3_lifecycle_columns_still_change(sessions: sessionmaker[Session]) -> None:
    version_id = a_version(sessions)
    with sessions() as s:
        s.execute(
            text("UPDATE sbam.sow_versions SET state = 'IN_REVIEW' WHERE id = :id"),
            {"id": version_id},
        )
        s.commit()
        state = s.execute(
            text("SELECT state FROM sbam.sow_versions WHERE id = :id"), {"id": version_id}
        ).scalar_one()
    assert state == "IN_REVIEW"


def test_e6_378_a_sow_stored_in_postgres_reads_back_in_section_order_and_validates(
    sessions: sessionmaker[Session],
) -> None:
    """JSONB keeps no key order; the column type restores it, so the stored SOW validates."""
    version_id = a_version(sessions)
    with sessions() as s:
        raw = s.execute(
            text("SELECT structured FROM sbam.sow_versions WHERE id = :id"), {"id": version_id}
        ).scalar_one()
        assert tuple(raw) != SECTIONS  # the database really did re-order the keys
        row = s.get(SowVersion, version_id)
        assert row is not None and row.structured is not None
        assert tuple(row.structured) == SECTIONS
        assert SowDocument.model_validate(row.structured).model_dump(mode="json") == DOC
