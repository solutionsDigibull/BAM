"""E11.2 against the Compose Postgres: migration 0017 up and down, the row constraints, tenant
scoping and the once-per-stage-key insert. A scratch database is created and dropped. Needs
`make up`; not part of the unit run."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sbam_api.builds.models import CodeAttempt
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.db.migrate import config
from sbam_api.db.repository import TenantContext
from sbam_api.db.session import make_engine, make_sessionmaker
from sqlalchemy import pool, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e112_{uuid.uuid4().hex[:10]}"
TENANT, OTHER, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


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
            command.downgrade(config(), "0016_knowledge_versions")  # 0017 undoes itself
            with engine.connect() as conn:
                found = conn.execute(text("SELECT to_regclass('sbam.code_attempts')")).scalar()
            assert found is None
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def values(sub_skill_id: uuid.UUID, **over: Any) -> dict[str, Any]:
    return {
        "workflow_id": uuid.uuid4(),
        "sub_skill_id": sub_skill_id,
        "build_no": 1,
        "cycle": 0,
        "attempt": 0,
        "template_name": "python-tool",
        "template_version": "1.0.0",
        "template_sha256": "a" * 64,
        "status": "BODIES_GENERATED",
        "bodies": {"action_body": "return None", "check_body": "return None"},
        "llm_call_ids": [str(uuid.uuid4())],
        "trace_id": uuid.uuid4().hex,
        "findings": [],
        "created_by": "test",
        **over,
    }


def repo(session: Session, tenant: uuid.UUID = TENANT) -> CodeAttemptRepository:
    return CodeAttemptRepository(session, TenantContext(tenant, PROJECT))


def test_e11_2_a_row_round_trips_once_per_stage_key(sessions: sessionmaker[Session]) -> None:
    sub_skill = uuid.uuid4()
    with sessions() as session:
        first = repo(session).record(values(sub_skill))
        session.commit()
    with sessions() as session:
        again = repo(session).record(values(sub_skill, status="PRECHECK_FAILED"))
        assert again.id == first.id and again.status == "BODIES_GENERATED"
        found = repo(session).find(sub_skill, 1, 0, 0)
        assert found is not None and found.bodies["action_body"] == "return None"


def test_e11_2_other_tenant_does_not_see_the_row(sessions: sessionmaker[Session]) -> None:
    sub_skill = uuid.uuid4()
    with sessions() as session:
        repo(session).record(values(sub_skill))
        session.commit()
    with sessions() as session:
        assert repo(session, OTHER).find(sub_skill, 1, 0, 0) is None


@pytest.mark.parametrize("over", [{"status": "PASSED"}, {"attempt": 3}, {"attempt": -1}])
def test_e11_2_constraints_reject_bad_rows(
    sessions: sessionmaker[Session], over: dict[str, Any]
) -> None:
    with sessions() as session, pytest.raises(IntegrityError):
        repo(session).add(CodeAttempt(**values(uuid.uuid4(), **over)))


def test_e11_2_duplicate_stage_key_is_rejected_by_the_database(
    sessions: sessionmaker[Session],
) -> None:
    sub_skill = uuid.uuid4()
    with sessions() as session:
        repo(session).record(values(sub_skill))
        session.commit()
    with sessions() as session, pytest.raises(IntegrityError):
        repo(session).add(CodeAttempt(**values(sub_skill)))
