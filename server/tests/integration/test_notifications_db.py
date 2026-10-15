"""E10.4 against the Compose Postgres: migration 0009 up and down, the dedupe unique key, and the
type CHECK. A scratch database is created and dropped. Needs `make up`; not part of the unit run."""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.notifications.repository import NotificationRepository
from sqlalchemy import pool, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e104_{uuid.uuid4().hex[:10]}"
TENANT, USER = uuid.uuid4(), uuid.uuid4()


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield make_sessionmaker(engine)
            command.downgrade(config(), "0008_rbac")  # 0009 undoes exactly itself
            with engine.connect() as conn:
                found = conn.execute(text("SELECT to_regclass('sbam.notifications')")).scalar()
            assert found is None
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def values(activity: str, type: str = "STAGE_COMPLETED") -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "user_id": USER,
        "type": type,
        "title": "t",
        "body": "b",
        "temporal_workflow_id": "wf",
        "activity_id": activity,
    }


def test_e10_4_t03_insert_if_new_writes_once_per_workflow_activity_user(
    sessions: sessionmaker[Session],
) -> None:
    with sessions() as session:
        repo = NotificationRepository(session, TenantContext(TENANT))
        assert repo.insert_if_new(values("a1")) is True
        assert repo.insert_if_new(values("a1")) is False  # the retry
        assert repo.insert_if_new(values("a2")) is True
        session.commit()
        assert len(repo.page(USER, unread_only=False, limit=10)) == 2


def test_e10_4_type_check_rejects_an_unknown_type(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        repo = NotificationRepository(session, TenantContext(TENANT))
        with pytest.raises(IntegrityError):
            repo.insert_if_new(values("a3", type="EMAIL_SENT"))
        session.rollback()
