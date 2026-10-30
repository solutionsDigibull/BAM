"""#497 (A-23c): a cached model answer nobody has used for the TTL is deleted, in every tenant; a
hit keeps it; the job is bounded, idempotent and writes one audit row of counts."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from llm_db import TENANT, llm_sessions
from sbam_api.audit.models import AuditLog
from sbam_api.db.repository import TenantContext
from sbam_api.llm import derivative_sweep
from sbam_api.llm.derivative_sweep import sweep_llm_derivatives
from sbam_api.llm.models import LlmDerivative
from sbam_api.llm.repository import LlmDerivativeRepository
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime.now(UTC)
OTHER = uuid.uuid4()


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    found: sessionmaker[Session] = llm_sessions()
    AuditLog.__table__.create(found.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
    return found


def add(
    sessions: sessionmaker[Session],
    key: str,
    *,
    created: timedelta,
    hit: timedelta | None = None,
    tenant: uuid.UUID = TENANT,
    deleted: bool = False,
) -> None:
    with sessions() as session:
        row = LlmDerivative(
            tenant_id=tenant,
            cache_key=key * 64,
            alias="sbam-sow",
            prompt_id="smoke",
            prompt_version="1",
            output={"count": 1, "label": key},
            cost_usd=Decimal("0.5"),
            created_at=NOW - created,
            last_hit_at=None if hit is None else NOW - hit,
            deleted_at=NOW if deleted else None,
        )
        session.add(row)
        session.commit()


def keys(sessions: sessionmaker[Session]) -> list[str]:
    with sessions() as session:
        found = session.scalars(select(LlmDerivative.cache_key).order_by(LlmDerivative.cache_key))
        return list(found)


def sweep(sessions: sessionmaker[Session], *, ttl: int = 90, as_of: datetime = NOW) -> int:
    with sessions() as session:
        return sweep_llm_derivatives(session, as_of, ttl, "corr").deleted


def test_a23c_t01_only_answers_unused_for_the_ttl_are_deleted_in_every_tenant(
    sessions: sessionmaker[Session],
) -> None:
    add(sessions, "a", created=timedelta(days=200), hit=timedelta(days=1))  # used yesterday
    add(sessions, "b", created=timedelta(days=100))  # never hit, older than the TTL
    add(sessions, "c", created=timedelta(days=200), hit=timedelta(days=100))  # last hit too old
    add(sessions, "d", created=timedelta(days=10))  # new, never hit
    add(sessions, "e", created=timedelta(days=300), deleted=True)  # soft-deleted still holds text
    add(sessions, "f", created=timedelta(days=100), tenant=OTHER)  # another tenant's
    assert sweep(sessions) == 4
    assert keys(sessions) == ["a" * 64, "d" * 64]


def test_a23c_t02_a_hit_refreshes_last_use_so_the_answer_survives(
    sessions: sessionmaker[Session],
) -> None:
    add(sessions, "a", created=timedelta(days=100))
    with sessions() as session:
        repo = LlmDerivativeRepository(session, TenantContext(TENANT, None))
        row = repo.by_key("a" * 64)
        assert row is not None
        repo.hit(row)
        session.commit()
    assert sweep(sessions, as_of=NOW + timedelta(days=80)) == 0  # moved clock: last hit 80 days ago
    assert sweep(sessions, as_of=NOW + timedelta(days=91)) == 1  # past the TTL since that hit


def test_a23c_t03_a_ttl_of_zero_keeps_everything(sessions: sessionmaker[Session]) -> None:
    add(sessions, "a", created=timedelta(days=1000))
    assert sweep(sessions, ttl=0) == 0 and keys(sessions) == ["a" * 64]


def test_a23c_t04_each_run_is_bounded_and_the_next_one_continues(
    sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(derivative_sweep, "MAX_DELETES", 2)
    for key in "abcde":
        add(sessions, key, created=timedelta(days=100))
    assert [sweep(sessions) for _ in range(4)] == [2, 2, 1, 0]  # idempotent once it is empty


def test_a23c_t05_one_audit_row_of_counts_only_for_a_run_that_deleted(
    sessions: sessionmaker[Session],
) -> None:
    add(sessions, "a", created=timedelta(days=100))
    sweep(sessions)
    sweep(sessions)  # nothing left: no second row
    with sessions() as session:
        [row] = session.scalars(select(AuditLog)).all()
        assert row.action == "LLM_DERIVATIVES_SWEPT" and row.tenant_id is None
        assert row.after == {"ttl_days": 90, "deleted": 1}  # no key, no content
        assert session.scalar(select(func.count()).select_from(LlmDerivative)) == 0
