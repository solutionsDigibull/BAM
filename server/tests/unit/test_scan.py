"""E4.3: the scan step against SQLite, a temp-dir storage and a fake clamd on a real socket.

The real ClamAV and Temporal's retry timing are covered in tests/integration and tests/temporal.
"""

import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from clamd_fake import FakeClamd, version_string
from fixtures import eicar_bytes
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.core.ids import uuid7
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.ingest.rules import is_downloadable
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.documents import ScanStatus
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest import scan as scan_module
from sbam_workers.ingest.clamd import ClamdClient, ClamdError, SignatureStaleError
from sbam_workers.ingest.scan import NOT_FOUND, NOT_SCANNABLE, ScanDeps, mark_failed, scan_version
from sqlalchemy import Engine, pool, select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()
PDF = b"%PDF-1.7 a small clean document\n" * 50


@pytest.fixture
def root() -> Iterator[Path]:
    """A short root: keys are ~150 characters, and pytest's tmp_path can push them past MAX_PATH."""
    path = Path(tempfile.mkdtemp(prefix="sc"))
    yield path

    def writable(func: object, name: str, _: object) -> None:
        os.chmod(name, stat.S_IWRITE)  # stored objects are 0444
        os.unlink(name)

    shutil.rmtree(path, onexc=writable)


def sqlite_sessions() -> sessionmaker[Session]:
    engine: Engine = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    for table in (Document, DocumentVersion, AuditLog, NotificationOutbox):
        table.__table__.create(engine)  # type: ignore[attr-defined]
    return make_sessionmaker(engine)


class Harness:
    def __init__(self, root: Path, server: FakeClamd) -> None:
        self.sessions = sqlite_sessions()
        self.storage: StorageBackend = LocalDiskBackend(root)
        self.server = server
        self.deps = ScanDeps(
            self.sessions,
            self.storage,
            ClamdClient(server.host, server.port, timeout=5),
            max_signature_age=timedelta(hours=24),
        )

    def add_version(self, data: bytes = PDF, state: str = "UPLOADED", **fields: Any) -> StageRef:
        stored = self.storage.put(
            data, StorageScope(TENANT, PROJECT), Kind.UPLOADS, "application/pdf"
        )
        with self.sessions() as s:
            doc = Document(tenant_id=TENANT, project_id=PROJECT, title="spec.pdf")
            s.add(doc)
            s.flush()
            version = DocumentVersion(
                tenant_id=TENANT,
                project_id=PROJECT,
                document_id=doc.id,
                storage_key=fields.pop("storage_key", stored.storage_key),
                sha256=stored.sha256,
                size=stored.size,
                mime="application/pdf",
                state=state,
                **fields,
            )
            s.add(version)
            s.commit()
            return self.ref(version.id)

    @staticmethod
    def ref(version_id: uuid.UUID, tenant: uuid.UUID = TENANT) -> StageRef:
        return StageRef(
            tenant_id=tenant,
            project_id=PROJECT,
            run_id=uuid7(),
            correlation_id="e4-3-test",
            ref_id=version_id,
        )

    def version(self, version_id: uuid.UUID) -> DocumentVersion:
        with self.sessions() as s:
            row = s.get(DocumentVersion, version_id)
            assert row is not None
            s.expunge(row)
            return row

    def audit(self, version_id: uuid.UUID) -> list[str]:
        with self.sessions() as s:
            rows = s.scalars(select(AuditLog).where(AuditLog.entity_id == str(version_id)))
            return [r.action for r in rows]

    def outbox(self) -> list[str]:
        with self.sessions() as s:
            return [r.type for r in s.scalars(select(NotificationOutbox))]


@pytest.fixture
def server() -> Iterator[FakeClamd]:
    with FakeClamd() as fake:
        yield fake


@pytest.fixture
def h(root: Path, server: FakeClamd) -> Harness:
    return Harness(root, server)


def uploads_key(data: bytes) -> str:
    import hashlib

    return StorageScope(TENANT, PROJECT).key(Kind.UPLOADS, hashlib.sha256(data).hexdigest())


# --- T01: clean ---------------------------------------------------------------------------------


def test_e4_3_t01_clean_file_is_marked_clean_and_moves_on_to_parsing(h: Harness) -> None:
    inp = h.add_version()
    outcome = scan_version(h.deps, inp)
    assert outcome.verdict is ScanStatus.CLEAN
    assert outcome.signature_version and outcome.signature_version.startswith("ClamAV 1.5.4/")
    row = h.version(inp.ref_id)
    assert row.state == "PARSING" and row.scan_status == "CLEAN" and row.reject_code is None
    assert row.scanned_at is not None and row.scan_signature_version == outcome.signature_version
    assert h.audit(inp.ref_id) == ["scan_started", "scan_clean"]
    assert h.outbox() == ["document_version.scan_started", "document_version.scan_clean"]
    assert h.storage.exists(uploads_key(PDF))  # a clean file stays where it is
    assert is_downloadable(row.state, row.scan_status)


# --- T02: EICAR ---------------------------------------------------------------------------------


def test_e4_3_t02_eicar_is_rejected_with_malware_and_quarantined(h: Harness) -> None:
    data = eicar_bytes()  # the 68-byte test file, as ClamAV recognises it
    inp = h.add_version(data)
    outcome = scan_version(h.deps, inp)
    assert outcome.verdict is ScanStatus.INFECTED
    row = h.version(inp.ref_id)
    quarantine = StorageScope(TENANT, PROJECT).key(Kind.QUARANTINE, row.sha256)
    assert (row.state, row.scan_status, row.reject_code) == ("REJECTED", "INFECTED", "MALWARE")
    assert row.storage_key == quarantine
    assert h.storage.get(quarantine, verify=True) == data  # kept for investigation, hash intact
    assert not h.storage.exists(uploads_key(data))  # "uploads/ no longer contains the file"
    assert "document_version.malware_detected" in h.outbox()  # the "document rejected" notification
    assert h.audit(inp.ref_id) == ["scan_started", "malware_detected", "DOCUMENT_QUARANTINED"]
    assert not is_downloadable(row.state, row.scan_status)
    with h.sessions() as s:  # the audit row names the signature, never the file content
        quarantined = s.scalars(
            select(AuditLog).where(AuditLog.action == "DOCUMENT_QUARANTINED")
        ).one()
        after = quarantined.after or {}
        assert after["signature"] == "Win.Test.EICAR_HDB-1"
        assert "EICAR-STANDARD" not in str(after)


def test_e4_3_infected_file_is_kept_in_uploads_when_another_version_uses_it(h: Harness) -> None:
    data = eicar_bytes() + b"\n"  # a trailing newline still starts with the test string
    first = h.add_version(data)
    key = h.version(first.ref_id).storage_key
    h.add_version(data, state="DELETED", storage_key=key)  # a restorable deleted twin
    scan_version(h.deps, first)
    assert h.version(first.ref_id).state == "REJECTED"
    assert h.storage.exists(key)  # restoring the twin must still find its file


def test_e4_3_a_second_run_returns_the_same_answer_and_does_not_rescan(
    h: Harness, server: FakeClamd
) -> None:
    inp = h.add_version(eicar_bytes())
    first = scan_version(h.deps, inp)
    again = scan_version(h.deps, inp)
    assert again == first and server.scans == 1
    assert h.audit(inp.ref_id).count("malware_detected") == 1


def test_e4_3_failure_to_remove_the_original_does_not_undo_the_verdict(h: Harness) -> None:
    class StuckDelete:
        def __init__(self, inner: StorageBackend) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def delete(self, key: str) -> None:
            raise OSError("disk says no")

    inp = h.add_version(eicar_bytes())
    stuck = ScanDeps(h.sessions, StuckDelete(h.storage), h.deps.clamd, h.deps.max_signature_age)
    assert scan_version(stuck, inp).verdict is ScanStatus.INFECTED
    assert h.version(inp.ref_id).state == "REJECTED"
    assert h.storage.exists(uploads_key(eicar_bytes()))  # still there
    scan_version(h.deps, inp)  # the retry finishes the cleanup
    assert not h.storage.exists(uploads_key(eicar_bytes()))


# --- clamd trouble (T03 / T04 / T05 at activity level) -----------------------------------------


def test_e4_3_clamd_down_raises_a_retryable_error_and_the_retry_succeeds(
    root: Path, server: FakeClamd
) -> None:
    h = Harness(root, server)
    inp = h.add_version()
    dead = ScanDeps(
        h.sessions, h.storage, ClamdClient("127.0.0.1", 1, timeout=1), timedelta(hours=24)
    )
    for _ in range(2):  # two failed attempts: clamd is unreachable
        with pytest.raises(ClamdError):
            scan_version(dead, inp)
    row = h.version(inp.ref_id)
    assert row.state == "SCANNING" and row.scan_status is None  # never passed, never failed
    assert scan_version(h.deps, inp).verdict is ScanStatus.CLEAN  # clamd is back: attempt 3
    assert h.audit(inp.ref_id).count("scan_started") == 1  # the retries did not start it again


def test_e4_3_t05_stale_signatures_refuse_the_scan_and_read_nothing(root: Path) -> None:
    with FakeClamd(version=version_string(age=timedelta(hours=25))) as stale:
        h = Harness(root, stale)
        inp = h.add_version()
        with pytest.raises(SignatureStaleError):
            scan_version(h.deps, inp)
        assert stale.scans == 0  # the file was never sent
        assert h.version(inp.ref_id).state == "SCANNING"
        outcome = mark_failed(h.deps, inp)  # after the last attempt
    assert outcome.verdict is ScanStatus.SCAN_FAILED
    assert h.version(inp.ref_id).scan_status == "SCAN_FAILED"


def test_e4_3_mark_failed_sets_scan_failed_once_and_never_regresses(h: Harness) -> None:
    inp = h.add_version()
    with pytest.raises(ClamdError):
        scan_version(
            ScanDeps(
                h.sessions, h.storage, ClamdClient("127.0.0.1", 1, timeout=1), timedelta(hours=24)
            ),
            inp,
        )
    assert mark_failed(h.deps, inp).verdict is ScanStatus.SCAN_FAILED
    row = h.version(inp.ref_id)
    assert (row.state, row.scan_status) == ("SCAN_FAILED", "SCAN_FAILED")
    assert "document_version.scan_unavailable" in h.outbox()
    clean = h.add_version()
    scan_version(h.deps, clean)
    assert mark_failed(h.deps, clean).verdict is ScanStatus.CLEAN  # scanned meanwhile: unchanged
    assert h.version(clean.ref_id).state == "PARSING"


def test_e4_3_mark_failed_works_on_a_version_whose_scan_never_started(h: Harness) -> None:
    inp = h.add_version()
    assert mark_failed(h.deps, inp).verdict is ScanStatus.SCAN_FAILED
    assert h.version(inp.ref_id).state == "SCAN_FAILED"
    assert h.audit(inp.ref_id) == ["scan_started", "scan_unavailable"]


# --- permanent problems -------------------------------------------------------------------------


def test_e4_3_missing_file_is_a_non_retryable_not_found(h: Harness) -> None:
    inp = h.add_version()
    h.storage.delete(uploads_key(PDF))
    with pytest.raises(ApplicationError) as err:
        scan_version(h.deps, inp)
    assert err.value.type == NOT_FOUND and err.value.non_retryable


@pytest.mark.parametrize("state", ["PARSING", "PARSED", "REJECTED", "DELETED", "SCAN_FAILED"])
def test_e4_3_only_uploaded_or_scanning_versions_are_scanned(h: Harness, state: str) -> None:
    inp = h.add_version(state=state)
    with pytest.raises(ApplicationError) as err:
        scan_version(h.deps, inp)
    assert err.value.type == NOT_SCANNABLE and err.value.non_retryable
    assert h.server.scans == 0


def test_e4_3_unknown_version_and_other_tenants_version_are_not_found(h: Harness) -> None:
    inp = h.add_version()
    for ref in (h.ref(uuid.uuid4()), h.ref(inp.ref_id, tenant=uuid.uuid4())):
        with pytest.raises(ApplicationError) as err:
            scan_version(h.deps, ref)
        assert err.value.type == NOT_FOUND and err.value.non_retryable
    assert h.version(inp.ref_id).state == "UPLOADED"  # untouched


def test_e4_3_a_file_of_exactly_15_mb_is_scanned_whole(h: Harness, server: FakeClamd) -> None:
    inp = h.add_version(b"y" * 15_728_640)
    assert scan_version(h.deps, inp).verdict is ScanStatus.CLEAN
    assert server.sizes == [15_728_640]


# --- a fresh clamd starts with the database baked into its image ---------------------------------


@pytest.fixture(autouse=True)
def reload_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan_module, "_last_reload", float("-inf"))
    monkeypatch.setattr(scan_module, "RELOAD_WAIT", 0.2)


def test_e4_3_a_stale_baked_in_database_is_reloaded_once_and_the_scan_goes_ahead(
    root: Path,
) -> None:
    old = version_string(age=timedelta(days=18))
    with FakeClamd(
        version=old, version_after_reload=version_string(age=timedelta(hours=3))
    ) as fake:
        h = Harness(root, fake)
        inp = h.add_version()
        assert scan_version(h.deps, inp).verdict is ScanStatus.CLEAN
        assert fake.reloads == 1 and fake.scans == 1
        assert h.version(inp.ref_id).scan_signature_version == fake.version  # the reloaded one


def test_e4_3_a_database_that_is_stale_upstream_is_refused_and_reloaded_only_once(
    root: Path,
) -> None:
    old = version_string(age=timedelta(days=18))
    with FakeClamd(version=old) as fake:  # a reload changes nothing: the newest database is old
        h = Harness(root, fake)
        inp = h.add_version()
        for _ in range(3):
            with pytest.raises(SignatureStaleError):
                scan_version(h.deps, inp)
        assert fake.reloads == 1 and fake.scans == 0  # three attempts, one reload, no file sent


def test_e4_3_fresh_signatures_never_trigger_a_reload(h: Harness, server: FakeClamd) -> None:
    scan_version(h.deps, h.add_version())
    assert server.reloads == 0
