"""E1.5 repository base and conventions, without Postgres (in-memory SQLite)."""

import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from sbam_api.core import ids
from sbam_api.db import (
    ProjectOwned,
    StaleRowVersion,
    TenantContext,
    TenantContextMissing,
    TenantScopedRepository,
    status_check,
)
from sbam_api.db.base import NAMING_CONVENTION
from sbam_api.db.session import engine_url, make_engine
from sbam_contracts.state_machines import DocumentVersionState
from sqlalchemy import Boolean, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

TENANT_A, TENANT_B = uuid.uuid4(), uuid.uuid4()
PROJECT = uuid.uuid4()


class _Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)  # SQLite has no `sbam` schema


class Widget(ProjectOwned, _Base):
    __tablename__ = "widgets"
    __table_args__ = (status_check(DocumentVersionState),)
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(40), default=DocumentVersionState.UPLOADED.value)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)


class WidgetRepository(TenantScopedRepository[Widget]):
    model = Widget


@pytest.fixture
def session() -> Iterator[Session]:
    engine = make_engine("sqlite://")
    _Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def repo(session: Session, tenant: uuid.UUID = TENANT_A) -> WidgetRepository:
    return WidgetRepository(session, TenantContext(tenant, PROJECT))


def test_e1_5_t03_missing_context_raises_before_any_query() -> None:
    session = MagicMock(spec=Session)
    with pytest.raises(TenantContextMissing):
        WidgetRepository(session, None)
    assert session.method_calls == []


def test_e1_5_project_owned_add_needs_a_project_in_the_context(session: Session) -> None:
    tenant_only = WidgetRepository(session, TenantContext(TENANT_A))
    with pytest.raises(TenantContextMissing):
        tenant_only.add(Widget(name="w"))


def test_e1_5_t07_soft_delete_hides_the_row_unless_include_deleted(session: Session) -> None:
    r = repo(session)
    keep, gone = r.add(Widget(name="keep")), r.add(Widget(name="gone"))
    r.soft_delete(gone)
    assert gone.deleted_at is not None
    assert [w.id for w in r.list()] == [keep.id]
    assert r.get(gone.id) is None
    assert {w.id for w in r.list(include_deleted=True)} == {keep.id, gone.id}


def test_e1_5_t08_uuid7_keys_are_v7_and_sort_by_creation(session: Session) -> None:
    r = repo(session)
    made = [r.add(Widget(name=f"w{i}")).id for i in range(1000)]
    assert all(i.version == 7 for i in made)
    assert sorted(made) == made
    assert [w.id for w in r.list(limit=1000)] == made


def test_e1_5_uuid7_stays_ordered_when_the_clock_steps_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [2_000_000_000_000_000_000]
    monkeypatch.setattr("sbam_api.core.ids.time.time_ns", lambda: now[0])
    first = ids.uuid7()
    now[0] -= 5_000_000_000  # NTP steps the clock back 5 s
    later = [ids.uuid7() for _ in range(5000)]  # also runs the 12-bit counter past its end
    assert sorted([first, *later]) == [first, *later]


def test_e1_5_add_stamps_the_callers_tenant_and_other_tenants_see_nothing(
    session: Session,
) -> None:
    w = repo(session).add(Widget(name="w", tenant_id=TENANT_B))  # a forged tenant is overwritten
    assert w.tenant_id == TENANT_A
    assert repo(session, TENANT_B).get(w.id) is None


def test_e1_5_update_with_an_old_row_version_is_stale(session: Session) -> None:
    r = repo(session)
    w = r.add(Widget(name="v1"))
    assert w.row_version == 1
    w.name = "v2"
    r.update(w, expected_row_version=1)
    assert w.row_version == 2
    w.name = "v3"
    with pytest.raises(StaleRowVersion) as err:
        r.update(w, expected_row_version=1)
    assert err.value.status == 412


def test_e1_5_status_check_lists_the_enum_values() -> None:
    sql = str(status_check(DocumentVersionState).sqltext)
    assert all(f"'{s.value}'" in sql for s in DocumentVersionState)


def test_e1_5_database_url_gets_the_psycopg_driver() -> None:
    assert engine_url("postgresql://u:p@h:5432/d") == "postgresql+psycopg://u:p@h:5432/d"
    assert engine_url("sqlite://") == "sqlite://"
