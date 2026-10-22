"""E4.4: the `detect_type` step against SQLite and a temp-dir storage, on the golden fixtures.

The pure rules are in test_detect.py; the workflow around the step is in tests/temporal.
"""

import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.core.ids import uuid7
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.ingest.rules import is_downloadable
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest.type_detection import (
    NOT_DETECTABLE,
    NOT_FOUND,
    DetectDeps,
    detect_version,
)
from sqlalchemy import Engine, pool, select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

TENANT, OTHER_TENANT, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

PE = (
    b"MZ"
    + b"\x90" * 58
    + (0x80).to_bytes(4, "little")
    + b"\x00" * 64
    + b"PE\x00\x00"
    + b"\x00" * 200
)
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x21" + b"\x00" * 100


@pytest.fixture
def root() -> Iterator[Path]:
    """A short root: keys are ~150 characters, and pytest's tmp_path can push them past MAX_PATH."""
    path = Path(tempfile.mkdtemp(prefix="td"))
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
    def __init__(self, root: Path) -> None:
        self.sessions = sqlite_sessions()
        self.storage: StorageBackend = LocalDiskBackend(root)
        self.deps = DetectDeps(self.sessions, self.storage)

    def add(self, name: str, data: bytes, **fields: Any) -> StageRef:
        """A version the scan left CLEAN and PARSING, as the workflow hands it to `detect_type`."""
        stored = self.storage.put(data, StorageScope(TENANT, PROJECT), Kind.UPLOADS, "x/y")
        values: dict[str, Any] = {"state": "PARSING", "scan_status": "CLEAN"} | fields
        with self.sessions() as s:
            doc = Document(tenant_id=TENANT, project_id=PROJECT, title=name)
            s.add(doc)
            s.flush()
            version = DocumentVersion(
                tenant_id=TENANT,
                project_id=PROJECT,
                document_id=doc.id,
                storage_key=values.pop("storage_key", stored.storage_key),
                sha256=stored.sha256,
                size=stored.size,
                mime="application/octet-stream",
                **values,
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
            correlation_id="e4-4-test",
            ref_id=version_id,
        )

    def version(self, ref: StageRef) -> DocumentVersion:
        with self.sessions() as s:
            row = s.get(DocumentVersion, ref.ref_id)
            assert row is not None
            s.expunge(row)
            return row

    def audit(self) -> list[str]:
        with self.sessions() as s:
            return [r.action for r in s.scalars(select(AuditLog).order_by(AuditLog.id))]

    def outbox(self) -> list[str]:
        with self.sessions() as s:
            return [r.type for r in s.scalars(select(NotificationOutbox))]


@pytest.fixture
def h(root: Path) -> Harness:
    return Harness(root)


def docs(name: str) -> bytes:
    return fixture_path("documents", name).read_bytes()


def permanent(call: Callable[[], object]) -> str:
    with pytest.raises(ApplicationError) as caught:
        call()
    assert caught.value.non_retryable is True
    return caught.value.type or ""


# --- detected ------------------------------------------------------------------------------------


def test_e4_4_t01_a_text_pdf_is_stored_with_its_type_and_row_and_handed_on(h: Harness) -> None:
    ref = h.add("report.pdf", docs("text.pdf"))
    assert detect_version(h.deps, ref).ids == [ref.ref_id]
    version = h.version(ref)
    assert (version.detected_type, version.filetype_row_label) == ("pdf", "Filetype #21")
    assert version.state == "PARSING" and version.reject_code is None  # the loader goes next
    assert h.audit() == ["DOCUMENT_TYPE_DETECTED"]


def test_e4_4_a_retry_after_detection_answers_the_same_and_writes_nothing_new(h: Harness) -> None:
    ref = h.add("report.pdf", docs("text.pdf"))
    first = detect_version(h.deps, ref)
    again = detect_version(h.deps, h.ref(ref.ref_id))
    assert first.ids == again.ids == [ref.ref_id]
    assert h.audit() == ["DOCUMENT_TYPE_DETECTED"]


@pytest.mark.parametrize(
    ("name", "fixture", "kind", "row"),
    [
        ("letter.docx", "sample.docx", "docx", "Filetype #19"),
        ("budget.xlsx", "multi_sheet.xlsx", "xlsx", "Filetype #23"),
        ("bundle.zip", "sample.zip", "zip", "Filetype #24"),
        ("archive.tar.gz", "sample.tar.gz", "tar.gz", "Filetype #22"),
        ("rfq.msg", "with_attachment.msg", "msg", "Filetype #7"),
        ("notes.md", "sample.md", "md", "Filetype #6"),
    ],
)
def test_e4_4_containers_text_and_mail_are_detected_from_storage(
    h: Harness, name: str, fixture: str, kind: str, row: str
) -> None:
    ref = h.add(name, docs(fixture))
    assert detect_version(h.deps, ref).ids == [ref.ref_id]
    version = h.version(ref)
    assert (version.detected_type, version.filetype_row_label) == (kind, row)


# --- rejected ------------------------------------------------------------------------------------

# id -> (file name, content, reject code). Content is built lazily so the test ids stay short.
WRONG: dict[str, tuple[str, Callable[[], bytes], str]] = {
    "t02-pe-named-pdf": ("report.pdf", lambda: PE, "TYPE_MISMATCH"),
    "t03-docx-named-xlsx": ("budget.xlsx", lambda: docs("sample.docx"), "TYPE_MISMATCH"),
    "t04-bad-utf8-md": ("notes.md", lambda: b"# Title\n\xff\xfe broken\n", "INVALID_ENCODING"),
    "t08-mp3": ("song.mp3", lambda: MP3, "UNSUPPORTED_TYPE"),
    "png-named-pdf": ("fake.pdf", lambda: docs("fake_pdf_is_png.pdf"), "TYPE_MISMATCH"),
}


@pytest.mark.parametrize("case", list(WRONG))
def test_e4_4_a_wrong_type_is_rejected_with_its_code_and_a_notification(
    h: Harness, case: str
) -> None:
    name, data, code = WRONG[case]
    ref = h.add(name, data())
    assert detect_version(h.deps, ref).ids == []
    version = h.version(ref)
    assert (version.state, version.reject_code) == ("REJECTED", code)
    assert version.scan_status == "CLEAN" and version.detected_type is None
    assert "document_version.type_rejected" in h.outbox()  # the "document rejected" notification
    assert "DOCUMENT_TYPE_DETECTED" not in h.audit()


def test_e4_4_a_retry_after_a_rejection_answers_the_same_and_does_not_reject_twice(
    h: Harness,
) -> None:
    ref = h.add("report.pdf", PE)
    assert detect_version(h.deps, ref).ids == []
    assert detect_version(h.deps, h.ref(ref.ref_id)).ids == []
    assert h.outbox().count("document_version.type_rejected") == 1


def test_e4_4_a_rejected_file_is_not_downloadable(h: Harness) -> None:
    ref = h.add("report.pdf", PE)
    detect_version(h.deps, ref)
    version = h.version(ref)
    assert not is_downloadable(version.state, version.scan_status)


# --- the guards ----------------------------------------------------------------------------------


@pytest.mark.parametrize("scan_status", [None, "INFECTED", "SCAN_FAILED"])
def test_e4_4_nothing_is_read_unless_the_scan_was_clean(
    h: Harness, scan_status: str | None
) -> None:
    ref = h.add("report.pdf", docs("text.pdf"), scan_status=scan_status)
    assert permanent(lambda: detect_version(h.deps, ref)) == "NotScanned"
    assert h.version(ref).detected_type is None


@pytest.mark.parametrize("state", ["UPLOADED", "SCANNING", "PARSED", "SCAN_FAILED"])
def test_e4_4_a_version_that_is_not_parsing_cannot_be_type_checked(h: Harness, state: str) -> None:
    ref = h.add("report.pdf", docs("text.pdf"), state=state)
    assert permanent(lambda: detect_version(h.deps, ref)) == NOT_DETECTABLE


def test_e4_4_an_unknown_version_or_another_tenants_version_is_not_found(h: Harness) -> None:
    assert permanent(lambda: detect_version(h.deps, h.ref(uuid.uuid4()))) == NOT_FOUND
    ref = h.add("report.pdf", docs("text.pdf"))
    assert permanent(lambda: detect_version(h.deps, h.ref(ref.ref_id, OTHER_TENANT))) == NOT_FOUND


def test_e4_4_a_missing_stored_file_is_not_found_and_changes_nothing(h: Harness) -> None:
    key = f"{TENANT}/{PROJECT}/uploads/{'0' * 64}"
    ref = h.add("report.pdf", docs("text.pdf"), storage_key=key)
    assert permanent(lambda: detect_version(h.deps, ref)) == NOT_FOUND
    assert h.version(ref).state == "PARSING" and h.audit() == []
