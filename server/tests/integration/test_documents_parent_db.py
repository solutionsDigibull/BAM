"""E4.6 and E4.7 against the Compose Postgres: migrations 0026 and 0027, the parent of a document.

A scratch database is created and dropped. The FK keeps a parent real; the CHECK keeps a document
from being its own parent. Same tenant, same project and one level are the service's job."""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine
from sqlalchemy import Engine, pool, text
from sqlalchemy.exc import IntegrityError

from .test_db import url

pytestmark = pytest.mark.integration

DB = f"sbam_e46_{uuid.uuid4().hex[:10]}"
TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    eng = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", url(database=DB))
            command.upgrade(config(), "head")
            yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


def insert(
    engine: Engine, *, id: uuid.UUID | None = None, parent: uuid.UUID | None = None
) -> uuid.UUID:
    id = id or uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sbam.documents (id, tenant_id, project_id, title, parent_document_id,"
                " row_version, created_at, updated_at)"
                " VALUES (:id, :tenant, :project, 'a.pdf', :parent, 1, now(), now())"
            ),
            {"id": id, "tenant": TENANT, "project": PROJECT, "parent": parent},
        )
    return id


def test_e4_6_a_child_document_points_at_its_parent(engine: Engine) -> None:
    parent = insert(engine)
    child = insert(engine, parent=parent)
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT parent_document_id FROM sbam.documents WHERE id = :id"), {"id": child}
        ).scalar()
    assert found == parent


def test_e4_6_a_parent_that_does_not_exist_is_refused(engine: Engine) -> None:
    with pytest.raises(IntegrityError):
        insert(engine, parent=uuid.uuid4())


def test_e4_6_a_document_cannot_be_its_own_parent(engine: Engine) -> None:
    same = uuid.uuid4()
    with pytest.raises(IntegrityError) as caught:
        insert(engine, id=same, parent=same)
    assert "parent_not_self" in str(caught.value)


def test_e4_6_migration_0026_goes_down_and_up_again(engine: Engine) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url(database=DB))
        command.downgrade(config(), "0025_workflows")
        with engine.connect() as conn:
            gone = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'sbam'"
                    " AND table_name = 'documents' AND column_name = 'parent_document_id'"
                )
            ).scalar()
        assert gone == 0
        command.upgrade(config(), "head")
    insert(engine)  # the table works again


def test_e4_7_a_member_document_keeps_its_path_in_the_archive(engine: Engine) -> None:
    archive = insert(engine)
    member = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sbam.documents (id, tenant_id, project_id, title, parent_document_id,"
                " archive_path, row_version, created_at, updated_at)"
                " VALUES (:id, :tenant, :project, 'q3.csv', :parent, :path, 1, now(), now())"
            ),
            {
                "id": member,
                "tenant": TENANT,
                "project": PROJECT,
                "parent": archive,
                "path": "reports/q3.csv",
            },
        )
        found = conn.execute(
            text("SELECT archive_path FROM sbam.documents WHERE id = :id"), {"id": member}
        ).scalar()
    assert found == "reports/q3.csv"


def test_e4_7_migration_0027_goes_down_and_up_again(engine: Engine) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url(database=DB))
        command.downgrade(config(), "0026_document_parent")
        with engine.connect() as conn:
            gone = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'sbam'"
                    " AND table_name = 'documents' AND column_name = 'archive_path'"
                )
            ).scalar()
        assert gone == 0
        command.upgrade(config(), "head")
    insert(engine)  # the table works again
