"""E4.12: cleaning up abandoned uploads, against SQLite and a temp staging directory."""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.ingest import cleanup
from sbam_api.ingest.cleanup import cleanup_abandoned_uploads
from sbam_api.ingest.models import Document, DocumentVersion, UploadReservation
from sbam_api.ingest.rules import ABANDONED_ROW_RETENTION, ORPHAN_GRACE, UPLOAD_HOLD
from sbam_contracts.documents import CleanupResult
from sqlalchemy import Engine, func, pool, select
from sqlalchemy.orm import Session, sessionmaker

AS_OF = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
TENANT_A, TENANT_B, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CORRELATION = "e4-12-test"


class Harness:
    def __init__(self, staging: Path) -> None:
        engine: Engine = make_engine(
            "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
        ).execution_options(schema_translate_map={"sbam": None})
        for table in (Document, DocumentVersion, UploadReservation, AuditLog):
            table.__table__.create(engine)  # type: ignore[attr-defined]
        self.sessions: sessionmaker[Session] = make_sessionmaker(engine)
        self.staging = staging
        staging.mkdir()

    def reservation(
        self,
        status: str = "CREATED",
        ended: timedelta = timedelta(hours=1),
        tenant: uuid.UUID = TENANT_A,
        staged: bool = True,
    ) -> uuid.UUID:
        """A reservation whose hold ended `ended` ago (negative: still running) and its files."""
        row = UploadReservation(
            tenant_id=tenant,
            project_id=PROJECT,
            batch_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="spec.pdf",
            size=10,
            mime="application/pdf",
            status=status,
            expires_at=AS_OF - ended,
        )
        with self.sessions() as s:
            s.add(row)
            s.commit()
            row_id = row.id
        if staged:
            (self.staging / str(row_id)).write_bytes(b"partial bytes")
            (self.staging / f"{row_id}.info").write_text("{}")
        return row_id

    def run(self, as_of: datetime = AS_OF) -> CleanupResult:
        with self.sessions() as s:
            return cleanup_abandoned_uploads(s, self.staging, as_of, CORRELATION)

    def rows(self) -> set[uuid.UUID]:
        with self.sessions() as s:
            return set(s.scalars(select(UploadReservation.id)))

    def audit(self) -> list[AuditLog]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditLog).where(AuditLog.action == "UPLOADS_CLEANED")))

    def staged(self) -> set[str]:
        return {p.name for p in self.staging.iterdir()}


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path / "uploads")


def test_e4_12_t01_an_abandoned_upload_loses_its_staged_files(h: Harness) -> None:
    rid = h.reservation("CREATED", ended=timedelta(hours=1))
    result = h.run()
    assert result.files_removed == 2 and result.failed == 0 and result.rows_deleted == 0
    assert h.staged() == set()  # the file and its .info
    assert rid in h.rows()  # the row stays until its retention is over


def test_e4_12_a_reservation_that_never_started_is_abandoned_too(h: Harness) -> None:
    rid = h.reservation("RESERVED", ended=timedelta(minutes=1), staged=False)
    assert h.run().files_removed == 0 and rid in h.rows()


def test_e4_12_t02_a_running_upload_inside_its_hold_is_untouched(h: Harness) -> None:
    rid = h.reservation("CREATED", ended=-timedelta(hours=2))  # the hold ends in two hours
    result = h.run()
    assert result == CleanupResult()
    assert rid in h.rows() and len(h.staged()) == 2
    assert h.audit() == []  # nothing happened, nothing is written


@pytest.mark.parametrize("status", ["FINISHED", "DUPLICATE"])
def test_e4_12_t03_finished_uploads_are_never_touched(h: Harness, status: str) -> None:
    rid = h.reservation(status, ended=ABANDONED_ROW_RETENTION + timedelta(days=30))
    result = h.run()
    assert result == CleanupResult(orphans=0)
    assert rid in h.rows() and len(h.staged()) == 2  # even a very old one, even with files left


def test_e4_12_documents_and_versions_are_never_touched(h: Harness) -> None:
    with h.sessions() as s:
        doc = Document(tenant_id=TENANT_A, project_id=PROJECT, title="kept.pdf")
        s.add(doc)
        s.flush()
        s.add(
            DocumentVersion(
                tenant_id=TENANT_A,
                project_id=PROJECT,
                document_id=doc.id,
                storage_key="k",
                sha256="0" * 64,
                size=1,
                mime="application/pdf",
                state="UPLOADED",
            )
        )
        s.commit()
    h.reservation("CREATED", ended=ABANDONED_ROW_RETENTION + timedelta(days=1))
    h.run()
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(Document)) == 1
        assert s.scalar(select(func.count()).select_from(DocumentVersion)) == 1


def test_e4_12_t04_running_it_twice_changes_nothing_the_second_time(h: Harness) -> None:
    h.reservation("CREATED", ended=timedelta(hours=2))
    first = h.run()
    second = h.run()
    assert first.files_removed == 2 and second == CleanupResult()
    assert len(h.audit()) == 1  # the second run found nothing to say


def test_e4_12_t05_rows_are_deleted_after_the_retention_and_the_run_is_audited(
    h: Harness,
) -> None:
    old = h.reservation("CREATED", ended=ABANDONED_ROW_RETENTION + timedelta(hours=1))
    young = h.reservation("CREATED", ended=ABANDONED_ROW_RETENTION - timedelta(hours=1))
    never_started = h.reservation(
        "RESERVED", ended=ABANDONED_ROW_RETENTION + timedelta(days=2), staged=False
    )
    result = h.run()
    assert result.rows_deleted == 2 and result.files_removed == 4
    assert h.rows() == {young}  # kept: its retention is not over
    assert old not in h.rows() and never_started not in h.rows()
    (entry,) = h.audit()
    assert entry.tenant_id is None and entry.actor_id == "SYSTEM"
    assert entry.after == {"files_removed": 4, "rows_deleted": 2, "orphans": 0, "failed": 0}
    assert entry.correlation_id == CORRELATION


def test_e4_12_t06_a_staged_file_with_no_reservation_is_reported_not_deleted(h: Harness) -> None:
    orphan = uuid.uuid4()
    old = (AS_OF - UPLOAD_HOLD - ORPHAN_GRACE - timedelta(hours=2)).timestamp()
    for name in (str(orphan), f"{orphan}.info"):
        path = h.staging / name
        path.write_bytes(b"x")
        os.utime(path, (old, old))
    young = h.staging / str(uuid.uuid4())  # a recent file may belong to an upload in flight
    young.write_bytes(b"x")
    os.utime(young, (AS_OF.timestamp(), AS_OF.timestamp()))
    (h.staging / "not-a-reservation.lock").write_bytes(b"x")  # not one of ours
    owned = h.reservation("FINISHED", ended=timedelta(days=30))  # its file is not an orphan
    os.utime(h.staging / str(owned), (old, old))
    result = h.run()
    assert result.orphans == 2  # the file and its .info
    assert (h.staging / str(orphan)).exists() and young.exists()  # nothing deleted
    assert (h.staging / "not-a-reservation.lock").exists()


def test_e4_12_a_file_that_cannot_be_removed_is_counted_and_does_not_stop_the_rest(
    h: Harness,
) -> None:
    stuck = h.reservation(
        "CREATED", ended=ABANDONED_ROW_RETENTION + timedelta(days=1), staged=False
    )
    (h.staging / str(stuck)).mkdir()  # unlink() refuses a directory: stands in for a disk error
    fine = h.reservation("CREATED", ended=ABANDONED_ROW_RETENTION + timedelta(days=1))
    result = h.run()
    assert result.failed == 1 and result.files_removed == 2 and result.rows_deleted == 1
    assert stuck in h.rows() and fine not in h.rows()  # the stuck one is kept for the next run
    (entry,) = h.audit()
    assert (entry.after or {})["failed"] == 1


def test_e4_12_every_tenant_is_cleaned(h: Harness) -> None:
    a = h.reservation("CREATED", ended=timedelta(hours=2), tenant=TENANT_A)
    b = h.reservation("CREATED", ended=timedelta(hours=2), tenant=TENANT_B)
    assert h.run().files_removed == 4 and h.staged() == set()
    assert {a, b} <= h.rows()


def test_e4_12_it_works_through_more_rows_than_one_chunk(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cleanup, "CHUNK", 40)
    for _ in range(130):
        h.reservation("CREATED", ended=timedelta(hours=2))
    assert h.run().files_removed == 260


def test_e4_12_a_bounded_run_cannot_starve_old_rows_the_next_run_finishes_them(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cleanup, "CHUNK", 50)
    monkeypatch.setattr(cleanup, "MAX_ROWS", 100)
    past = ABANDONED_ROW_RETENTION + timedelta(days=1)
    for _ in range(130):
        h.reservation("CREATED", ended=past)
    first = h.run()
    assert first.rows_deleted == 100 and len(h.rows()) == 30  # the bound
    second = h.run()
    assert second.rows_deleted == 30 and h.rows() == set() and h.staged() == set()


def test_e4_12_inside_the_retention_the_newest_abandonments_are_cleaned_first(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cleanup, "CHUNK", 10)
    monkeypatch.setattr(cleanup, "MAX_ROWS", 10)
    ids = [h.reservation("CREATED", ended=timedelta(hours=3)) for _ in range(25)]
    h.run()
    left = {name for name in h.staged() if not name.endswith(".info")}
    assert left == {str(rid) for rid in ids[:15]}  # the 10 newest are done, the oldest wait


def test_e4_12_a_missing_staging_directory_is_not_an_error(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "uploads")
    harness.staging.rmdir()
    assert harness.run() == CleanupResult()
