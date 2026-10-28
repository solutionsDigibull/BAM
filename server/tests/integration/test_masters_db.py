"""E3.2 on the Compose Postgres: the migration, the append-only trigger and a real version race.

A scratch database migrated to head; dropped at the end.
"""

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.masters import service
from sbam_api.masters.models import MasterDocument, MasterEntryVersion
from sbam_api.masters.snapshot import snapshot_hash
from sbam_api.masters.sql import APPEND_ONLY_SQLSTATE
from sbam_api.projects.models import Project
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_api import MasterEntryCreate, MasterVersionCreate, SnapshotEntry
from sqlalchemy import pool, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e32_{uuid.uuid4().hex[:10]}"
TENANT = uuid.uuid4()
ADMIN = CurrentUser(
    user_id=uuid.uuid4(), keycloak_sub="admin-a", tenant_id=TENANT, roles=frozenset()
)
RULE_BOM = {"domain_rule": "RULE-BOM: applies to BOM. Policy: compare", "misc": "Status: Proposed"}


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


def new_entry(sessions: sessionmaker[Session], payload: dict[str, str] = RULE_BOM) -> uuid.UUID:
    with sessions() as s:
        entry, _ = service.create_entry(
            s, ADMIN, MasterType.RULES, MasterEntryCreate(payload=payload)
        )
        return entry.id


def test_e3_2_t03_versions_reject_update_delete_and_truncate(
    sessions: sessionmaker[Session],
) -> None:
    entry_id = new_entry(sessions, {**RULE_BOM, "domain_rule": "RULE-T03: x"})
    with sessions() as s:  # a snapshot row, so the row triggers have something to refuse
        project = Project(tenant_id=TENANT, name="T03", status="ACTIVE", state="IDLE")
        s.add(project)
        s.commit()
        service.create_snapshot(s, ADMIN, project.id)
    for statement in (
        "UPDATE sbam.master_entry_versions SET payload = '{}'::jsonb",
        "DELETE FROM sbam.master_entry_versions",
        "TRUNCATE sbam.master_entry_versions CASCADE",
        "UPDATE sbam.master_snapshots SET sha256 = 'x'",
        "DELETE FROM sbam.master_snapshots",
    ):
        with sessions() as s:
            with pytest.raises(DBAPIError) as raised:
                s.execute(text(statement))
            assert getattr(raised.value.orig, "sqlstate", None) == APPEND_ONLY_SQLSTATE, statement
            s.rollback()
    with sessions() as s:
        row = s.scalars(
            select(MasterEntryVersion).where(MasterEntryVersion.entry_id == entry_id)
        ).one()
    assert row.payload["domain_rule"] == "RULE-T03: x"


def test_e3_2_t06_concurrent_versions_one_wins_one_409(sessions: sessionmaker[Session]) -> None:
    entry_id = new_entry(sessions, {**RULE_BOM, "domain_rule": "RULE-T06: x"})
    results: list[int] = []
    gate = threading.Barrier(2)

    def write(misc: str) -> None:
        gate.wait()
        with sessions() as s:
            try:
                service.new_version(
                    s,
                    ADMIN,
                    MasterType.RULES,
                    entry_id,
                    MasterVersionCreate(payload={"domain_rule": "RULE-T06: x", "misc": misc}),
                    '"1"',
                )
                results.append(201)
            except ApiError as exc:
                assert exc.code == "VERSION_CONFLICT"
                results.append(exc.status)

    threads = [threading.Thread(target=write, args=(m,)) for m in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [201, 409]
    with sessions() as s:
        versions = s.scalars(
            select(MasterEntryVersion.version).where(MasterEntryVersion.entry_id == entry_id)
        ).all()
    assert sorted(versions) == [1, 2]


def test_e3_2_migration_downgrades_and_upgrades_cleanly(
    sessions: sessionmaker[Session],
) -> None:
    command.downgrade(config(), "0009_document_scan")
    with sessions() as s:
        assert s.execute(text("SELECT to_regclass('sbam.master_entries')")).scalar() is None
    command.upgrade(config(), "head")
    with sessions() as s:
        assert s.execute(text("SELECT to_regclass('sbam.master_entries')")).scalar() is not None


def test_e3_8b_a_snapshot_pins_a_parsed_master_document_on_postgres(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    """The JSONB `documents` column round-trips, the hash covers it and the export verifies."""
    with sessions() as s:
        project = Project(tenant_id=TENANT, name="E3.8b", status="ACTIVE", state="IDLE")
        s.add(project)
        s.commit()
        sha = "c" * 64
        s.add(
            MasterDocument(
                tenant_id=TENANT,
                project_id=None,
                title="standard.pdf",
                filetype_row_label="Filetype #21",
                intent_class="Policy / rules",
                master_type="RULES",
                route_source="DEFAULT_BY_FILETYPE",
                storage_key=f"{TENANT}/{uuid.UUID(int=0)}/uploads/{sha}",
                sha256=sha,
                size=10,
                mime="application/pdf",
                scan_status="CLEAN",
                parse_status="PARSED",
                derived_md_key=f"{TENANT}/{uuid.UUID(int=0)}/derived/{'d' * 64}",
            )
        )
        s.commit()
        snap = service.create_snapshot(s, ADMIN, project.id)
        s.expire_all()
        pinned = service.snapshot_documents(s, ADMIN, snap.id)
        assert [d.title for d in pinned] == ["standard.pdf"] and pinned[0].sha256 == sha
        entries = [SnapshotEntry.model_validate(e) for e in snap.entries]
        assert snap.sha256 == snapshot_hash(project.id, entries, pinned)
        now = datetime(2026, 10, 27, tzinfo=UTC)
        report = service.export_master_context(
            s, ADMIN, snap.id, project.id, LocalDiskBackend(tmp_path), now
        )
        assert report.snapshot_id == snap.id
        assert service.context_outdated(s, ADMIN, snap.id) is False
