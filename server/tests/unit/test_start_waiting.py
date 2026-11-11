"""`StartWaitingIngestions`: which versions the sweep picks, and that it starts them once."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.db.base import utcnow
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.ingest import ingestion
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.ingest.repository import waiting_for_ingestion
from sqlalchemy import Engine, pool
from sqlalchemy.orm import Session, sessionmaker

TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()


def sessions() -> sessionmaker[Session]:
    engine: Engine = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    for table in (Document, DocumentVersion, AuditLog, NotificationOutbox):
        table.__table__.create(engine)  # type: ignore[attr-defined]
    return make_sessionmaker(engine)


def add(s: sessionmaker[Session], **fields: Any) -> uuid.UUID:
    with s() as session:
        doc = Document(tenant_id=TENANT, project_id=PROJECT, title="spec.pdf")
        session.add(doc)
        session.flush()
        version = DocumentVersion(
            tenant_id=TENANT,
            project_id=PROJECT,
            document_id=doc.id,
            storage_key="k",
            sha256="0" * 64,
            size=1,
            mime="application/pdf",
            **{"state": "UPLOADED", **fields},
        )
        session.add(version)
        session.commit()
        return version.id


def test_the_sweep_picks_only_old_unstarted_uploaded_versions() -> None:
    s = sessions()
    waiting = add(s)
    add(s, ingestion_started_at=utcnow())  # already handed to Temporal
    add(s, state="PARSED")
    add(s, deleted_at=utcnow())
    later = utcnow() + timedelta(minutes=2)
    with s() as session:
        assert waiting_for_ingestion(session, later, 10) == [(TENANT, PROJECT, waiting)]
        assert waiting_for_ingestion(session, utcnow() - timedelta(minutes=1), 10) == []  # too new


def test_the_sweep_starts_each_waiting_version_and_counts_a_failed_start(
    monkeypatch: Any,
) -> None:
    s = sessions()
    first, second = add(s), add(s)
    started: list[uuid.UUID] = []

    async def start(
        client: Any, sess: Any, ctx: Any, version_id: uuid.UUID, *_: Any, **__: Any
    ) -> Any:
        if version_id == second:
            return None  # Temporal unavailable for this one
        started.append(version_id)
        return object()

    monkeypatch.setattr(ingestion, "start_ingestion_run", start)
    later = utcnow() + timedelta(minutes=2)
    result = asyncio.run(ingestion.start_stale_waiting(object(), s, later))  # type: ignore[arg-type]
    assert result == (1, 1) and started == [first]


def test_a_start_that_keeps_failing_goes_to_the_back_and_the_others_get_their_turn(
    monkeypatch: Any,
) -> None:
    s = sessions()
    stuck = add(s)
    later = [add(s) for _ in range(3)]
    tried: list[uuid.UUID] = []

    async def start(
        client: Any, sess: Any, ctx: Any, version_id: uuid.UUID, *_: Any, **__: Any
    ) -> Any:
        tried.append(version_id)
        if version_id == stuck:
            return None
        ingestion._mark_started(sess, ctx, version_id, utcnow())  # what a real start records
        return object()

    monkeypatch.setattr(ingestion, "start_ingestion_run", start)

    def run() -> tuple[int, int]:
        before = utcnow() + timedelta(minutes=2)
        return asyncio.run(ingestion.start_stale_waiting(object(), s, before, limit=1))  # type: ignore[arg-type]

    assert run() == (0, 1) and tried == [stuck]  # the oldest is tried first and fails
    # it was touched, so the next sweep takes the next one instead of the stuck one again
    assert run() == (1, 0) and tried[-1] != stuck
    assert run() == (1, 0) and run() == (1, 0)
    assert set(tried[1:]) == set(later)
