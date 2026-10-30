"""A-15: the upload-slot lock on a real PostgreSQL. SQLite has one writer, so only PostgreSQL can
show that two requests for one project queue and two projects do not.

Needs a scratch PostgreSQL DSN in `SCHEMA_CHECK_TEST_DSN` (CI's unit job has one) and skips without
it. The lock is taken on a bare connection: it needs no table.
"""

import os
import threading
import uuid
from collections.abc import Iterator

import pytest
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine
from sbam_api.ingest.repository import ReservationRepository
from sqlalchemy import Engine, pool
from sqlalchemy.orm import Session, sessionmaker

DSN = os.environ.get("SCHEMA_CHECK_TEST_DSN")
pytestmark = pytest.mark.skipif(
    DSN is None, reason="SCHEMA_CHECK_TEST_DSN names no scratch PostgreSQL"
)
TENANT = uuid.uuid4()


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    assert DSN is not None
    engine: Engine = make_engine(
        DSN.replace("postgresql://", "postgresql+psycopg://", 1), poolclass=pool.NullPool
    )
    yield sessionmaker(engine)
    engine.dispose()


def locker(sessions: sessionmaker[Session], project: uuid.UUID) -> tuple[Session, threading.Event]:
    """A session that has asked for `project`'s lock, and an event set once it was granted."""
    session, granted = sessions(), threading.Event()

    def take() -> None:
        ReservationRepository(session, TenantContext(TENANT, project)).lock_slots()
        granted.set()

    thread = threading.Thread(target=take, daemon=True)
    thread.start()
    thread.join(timeout=0.5)  # a blocked session is still inside lock_slots
    return session, granted


def test_a15_two_requests_for_one_project_queue_on_the_lock(
    sessions: sessionmaker[Session],
) -> None:
    project = uuid.uuid4()
    first, first_granted = locker(sessions, project)
    second, second_granted = locker(sessions, project)
    try:
        assert first_granted.is_set()
        assert not second_granted.wait(timeout=1.5), "the second request got the lock too"
        first.commit()  # the first request's reservations are now visible to the second
        assert second_granted.wait(timeout=10)
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def test_a15_other_projects_are_not_held_up(sessions: sessionmaker[Session]) -> None:
    first, first_granted = locker(sessions, uuid.uuid4())
    other, other_granted = locker(sessions, uuid.uuid4())
    try:
        assert first_granted.is_set()
        assert other_granted.wait(timeout=5)
    finally:
        for session in (first, other):
            session.rollback()
            session.close()
