"""E1.5 database baseline against the Compose Postgres 16 (TD §19.3).

Each test works in its own scratch schema (or, for migrations, a scratch database) and drops it
afterwards, so the shared `sbam` database is never assumed empty or touched.
"""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sbam_api.db import (
    ProjectOwned,
    RowFrozen,
    StaleRowVersion,
    TenantContext,
    TenantScopedRepository,
    status_check,
)
from sbam_api.db.base import NAMING_CONVENTION
from sbam_api.db.frozen import create_frozen_function, create_frozen_trigger
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine
from sbam_contracts.state_machines import DocumentVersionState
from sqlalchemy import Boolean, Engine, MetaData, String, pool, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .conftest import port
from .test_profiles import env_value

pytestmark = pytest.mark.integration

SCRATCH = f"t_e15_{uuid.uuid4().hex[:10]}"
TENANT_A, TENANT_B, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def url(user: str = "sbam", database: str = "sbam") -> str:
    password = env_value("POSTGRES_PASSWORD" if user == "postgres" else "SBAM_DB_PASSWORD")
    return f"postgresql://{user}:{password}@127.0.0.1:{port('POSTGRES_PORT', 5432)}/{database}"


class _Base(DeclarativeBase):
    metadata = MetaData(schema=SCRATCH, naming_convention=NAMING_CONVENTION)


class Widget(ProjectOwned, _Base):
    __tablename__ = "widgets"
    __table_args__ = (status_check(DocumentVersionState),)
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(40), default=DocumentVersionState.UPLOADED.value)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)


class WidgetRepository(TenantScopedRepository[Widget]):
    model = Widget


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = make_engine(url())
    with eng.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{SCRATCH}"'))
        conn.execute(text(create_frozen_function(SCRATCH)))
    _Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text(create_frozen_trigger("widgets", SCRATCH)))
    yield eng
    with eng.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{SCRATCH}" CASCADE'))
    eng.dispose()


def add(engine: Engine, tenant: uuid.UUID = TENANT_A, **values: object) -> Widget:
    with Session(engine, expire_on_commit=False) as s:
        w = WidgetRepository(s, TenantContext(tenant, PROJECT)).add(Widget(name="w", **values))
        s.commit()
        return w


def test_e1_5_t01_upgrade_downgrade_upgrade_clean_and_no_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = f"sbam_e15_{uuid.uuid4().hex[:10]}"
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db}" OWNER sbam'))
    try:
        monkeypatch.setenv("DATABASE_URL", url(database=db))  # migrate as the app role
        cfg = config()
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        command.check(cfg)  # raises if the models and the database differ
        scratch = make_engine(url(database=db), poolclass=pool.NullPool)
        with scratch.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM sbam.alembic_version")).scalar()
            fn = conn.execute(text("SELECT to_regproc('sbam.forbid_frozen_change')")).scalar()
        assert version == "0002_temporal_runs"  # head; bump when a revision is added
        assert fn is not None
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)'))
        admin.dispose()


def test_e1_5_t02_other_tenant_gets_nothing(engine: Engine) -> None:
    theirs = add(engine, TENANT_B)
    with Session(engine) as s:
        mine = WidgetRepository(s, TenantContext(TENANT_A, PROJECT))
        assert mine.get(theirs.id) is None
        assert all(w.tenant_id == TENANT_A for w in mine.list())
        assert theirs.id not in {w.id for w in mine.list(include_deleted=True)}


def test_e1_5_t04_concurrent_update_second_writer_is_stale(engine: Engine) -> None:
    row = add(engine)
    ctx = TenantContext(TENANT_A, PROJECT)
    with Session(engine) as s1, Session(engine) as s2:
        r1, r2 = WidgetRepository(s1, ctx), WidgetRepository(s2, ctx)
        first, second = r1.get(row.id), r2.get(row.id)
        assert first is not None and second is not None
        first.name, second.name = "first", "second"
        r1.update(first, expected_row_version=1)
        s1.commit()
        assert first.row_version == 2
        with pytest.raises(StaleRowVersion):
            r2.update(second, expected_row_version=1)  # loaded at 1, but the row is now at 2
    with Session(engine) as s:
        assert WidgetRepository(s, ctx).get(row.id).name == "first"  # type: ignore[union-attr]


def test_e1_5_t05_frozen_row_rejects_update_and_delete(engine: Engine) -> None:
    row = add(engine, is_frozen=True)
    table = f'"{SCRATCH}".widgets'  # the test's own generated schema name, not input
    statements = (f"UPDATE {table} SET name = 'changed'", f"DELETE FROM {table}")  # noqa: S608
    for sql in statements:
        with engine.connect() as conn, pytest.raises(DBAPIError) as err:
            conn.execute(text(f"{sql} WHERE id = :id"), {"id": row.id})
        assert getattr(err.value.orig, "sqlstate", None) == "SB423"
    with Session(engine) as s:
        r = WidgetRepository(s, TenantContext(TENANT_A, PROJECT))
        frozen = r.get(row.id)
        assert frozen is not None and frozen.name == "w"  # unchanged
        frozen.name = "via repo"
        with pytest.raises(RowFrozen) as locked:
            r.update(frozen, expected_row_version=1)
        assert locked.value.status == 423


def test_e1_5_t06_status_outside_the_enum_violates_the_check(engine: Engine) -> None:
    with pytest.raises(IntegrityError) as err:
        add(engine, status="BOGUS")
    assert getattr(err.value.orig, "sqlstate", None) == "23514"  # check_violation
