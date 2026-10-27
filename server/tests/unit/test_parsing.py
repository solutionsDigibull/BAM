"""E4.5: the `parse_document` step against SQLite and a temp-dir storage, on the golden fixtures.

The loaders are in test_loaders_text.py; the workflow around the step is in tests/temporal.
"""

import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from fixtures import fixture_path
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.core.ids import uuid7
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.storage.base import Kind, StorageBackend, StorageScope, StoredObject
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import ParsedDocument
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest.parsing import (
    NOT_FOUND,
    NOT_PARSEABLE,
    ParseDeps,
    ParseFailure,
    mark_failed,
    parse_version,
)
from sqlalchemy import Engine, pool, select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

TENANT, OTHER_TENANT, PROJECT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
BOM_SEMICOLON = "﻿MPN;Qty\nSTM32;12\nRC0603;85\n".encode()


@pytest.fixture
def root() -> Iterator[Path]:
    """A short root: keys are ~150 characters, and pytest's tmp_path can push them past MAX_PATH."""
    path = Path(tempfile.mkdtemp(prefix="tp"))
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
    def __init__(self, root: Path, storage: StorageBackend | None = None) -> None:
        self.sessions = sqlite_sessions()
        self.storage: StorageBackend = storage or LocalDiskBackend(root)
        self.deps = ParseDeps(self.sessions, self.storage)

    def add(self, name: str, data: bytes, detected: str | None = "md", **fields: Any) -> StageRef:
        """A version the type check left PARSING: CLEAN scan, `detected_type` set."""
        stored = self.storage.put(data, StorageScope(TENANT, PROJECT), Kind.UPLOADS, "x/y")
        values: dict[str, Any] = {
            "state": "PARSING",
            "scan_status": "CLEAN",
            "detected_type": detected,
            "filetype_row_label": "Filetype #6",
        } | fields
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
            correlation_id="e4-5-test",
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


def derived(h: Harness, ref: StageRef) -> tuple[bytes, ParsedDocument]:
    version = h.version(ref)
    assert version.derived_md_key and version.derived_json_key
    doc = ParsedDocument.model_validate_json(h.storage.get(version.derived_json_key))
    return h.storage.get(version.derived_md_key), doc


# --- parsed ---------------------------------------------------------------------------------------


def test_e4_5_a_markdown_file_is_parsed_and_its_derivatives_are_stored(h: Harness) -> None:
    ref = h.add("rfq.md", docs("sample.md"))
    outcome = parse_version(h.deps, ref)
    assert outcome.parsed and outcome.code is None and outcome.document_version_id == ref.ref_id
    version = h.version(ref)
    assert version.state == "PARSED" and version.parse_code is None
    md, doc = derived(h, ref)
    assert md.decode().startswith("# NAV-9 RFQ summary") and doc.document_version_id == ref.ref_id
    assert doc.loader == "Filetype #6" and doc.blocks[0].line == 1
    assert version.parse_report == {
        "pages": 0,
        "sheets": 0,
        "tables": 0,
        "warnings": [],
        "ocr_needed_pages": [],
        "ocr_pages": [],
        "unreadable_pages": [],
        "attachments": [],
        "archive": None,
    }
    assert h.audit().count("DOCUMENT_PARSED") == 1 and "parse_completed" in h.audit()


def test_e4_5_t03_a_semicolon_csv_with_a_bom_gets_its_table_in_doc_json(h: Harness) -> None:
    ref = h.add("parts.csv", BOM_SEMICOLON, detected="csv", filetype_row_label="Filetype #18")
    assert parse_version(h.deps, ref).parsed
    md, doc = derived(h, ref)
    assert doc.tables[0].header == ["MPN", "Qty"] and doc.tables[0].rows[1] == ["RC0603", "85"]
    assert md.decode().startswith("| MPN | Qty |")
    assert h.version(ref).parse_report == {
        "pages": 0,
        "sheets": 0,
        "tables": 1,
        "warnings": [],
        "ocr_needed_pages": [],
        "ocr_pages": [],
        "unreadable_pages": [],
        "attachments": [],
        "archive": None,
    }


def test_e4_5_the_derivatives_live_under_the_tenant_and_project_prefix(h: Harness) -> None:
    ref = h.add("rfq.md", docs("sample.md"))
    parse_version(h.deps, ref)
    version = h.version(ref)
    for key in (version.derived_md_key, version.derived_json_key):
        assert key is not None and key.startswith(f"{TENANT}/{PROJECT}/{Kind.DERIVED.value}/")
        assert (key.rsplit("/", 1)[1]).isalnum()  # content hash, never a file name


def test_e4_5_a_retry_after_the_parse_repeats_the_answer_and_writes_nothing_new(
    h: Harness,
) -> None:
    ref = h.add("rfq.md", docs("sample.md"))
    first = parse_version(h.deps, ref)
    keys = (h.version(ref).derived_md_key, h.version(ref).derived_json_key)
    again = parse_version(h.deps, h.ref(ref.ref_id))
    assert first == again and again.parsed
    assert (h.version(ref).derived_md_key, h.version(ref).derived_json_key) == keys
    assert h.audit().count("DOCUMENT_PARSED") == 1


def test_e4_5_the_same_content_in_two_documents_is_one_stored_derivative(h: Harness) -> None:
    first, second = h.add("a.md", docs("sample.md")), h.add("b.md", docs("sample.md"))
    parse_version(h.deps, first)
    parse_version(h.deps, second)
    a, b = h.version(first), h.version(second)
    assert a.derived_md_key == b.derived_md_key  # content-addressed: an idempotent rewrite


def test_e4_5_one_bad_document_does_not_stop_the_next(h: Harness) -> None:
    bad, good = h.add("empty.md", b"  \n"), h.add("rfq.md", docs("sample.md"))
    assert not parse_version(h.deps, bad).parsed
    assert parse_version(h.deps, good).parsed
    assert (h.version(bad).state, h.version(good).state) == ("PARSE_FAILED", "PARSED")


# --- failed ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data", "detected", "code"),
    [
        ("empty.md", b"   \n\n", "md", ErrorCode.EMPTY),
        ("empty.csv", b"\n", "csv", ErrorCode.EMPTY),
        ("late.txt", (b"fine\n" * 3000) + b"\xff\xfe", "txt", ErrorCode.CORRUPT),
    ],
    ids=["empty-markdown", "empty-csv", "bad-utf8-after-the-head"],
)
def test_e4_5_a_file_the_loader_cannot_read_is_parse_failed_with_its_code(
    h: Harness, name: str, data: bytes, detected: str, code: ErrorCode
) -> None:
    ref = h.add(name, data, detected=detected)
    outcome = parse_version(h.deps, ref)
    assert (outcome.parsed, outcome.code) == (False, code)
    version = h.version(ref)
    assert (version.state, version.parse_code) == ("PARSE_FAILED", code.value)
    assert version.derived_md_key is None and version.derived_json_key is None
    assert version.parse_report is not None and version.parse_report["warnings"]
    assert "document_version.parse_failed" in h.outbox()  # the "document rejected" notification


def test_e4_5_a_retry_after_a_failure_repeats_the_answer_and_notifies_once(h: Harness) -> None:
    ref = h.add("empty.md", b"\n")
    first = parse_version(h.deps, ref)
    again = parse_version(h.deps, h.ref(ref.ref_id))
    assert first == again and again.code is ErrorCode.EMPTY
    assert h.outbox().count("document_version.parse_failed") == 1


def test_e4_5_a_type_with_no_loader_yet_stays_parsing(h: Harness) -> None:
    for kind in ("zip", "tar.gz"):
        ref = h.add(f"file.{kind}", b"anything", detected=kind)
        outcome = parse_version(h.deps, ref)
        assert (outcome.parsed, outcome.code, outcome.report) == (False, None, None)
        assert h.version(ref).state == "PARSING" and h.audit() == []


# --- the guards -----------------------------------------------------------------------------------


@pytest.mark.parametrize("scan_status", [None, "INFECTED", "SCAN_FAILED"])
def test_e4_5_nothing_is_opened_unless_the_scan_was_clean(
    h: Harness, scan_status: str | None
) -> None:
    ref = h.add("rfq.md", docs("sample.md"), scan_status=scan_status)
    assert permanent(lambda: parse_version(h.deps, ref)) == "NotScanned"
    assert h.version(ref).state == "PARSING"


@pytest.mark.parametrize("state", ["UPLOADED", "SCANNING", "REJECTED", "SCAN_FAILED"])
def test_e4_5_a_version_that_is_not_parsing_cannot_be_parsed(h: Harness, state: str) -> None:
    ref = h.add("rfq.md", docs("sample.md"), state=state)
    assert permanent(lambda: parse_version(h.deps, ref)) == NOT_PARSEABLE


def test_e4_5_a_version_whose_type_was_never_detected_cannot_be_parsed(h: Harness) -> None:
    ref = h.add("rfq.md", docs("sample.md"), detected=None)
    assert permanent(lambda: parse_version(h.deps, ref)) == NOT_PARSEABLE


def test_e4_5_an_unknown_version_or_another_tenants_version_is_not_found(h: Harness) -> None:
    assert permanent(lambda: parse_version(h.deps, h.ref(uuid.uuid4()))) == NOT_FOUND
    ref = h.add("rfq.md", docs("sample.md"))
    assert permanent(lambda: parse_version(h.deps, h.ref(ref.ref_id, OTHER_TENANT))) == NOT_FOUND


def test_e4_5_a_missing_stored_file_is_not_found_and_changes_nothing(h: Harness) -> None:
    key = f"{TENANT}/{PROJECT}/uploads/{'0' * 64}"
    ref = h.add("rfq.md", docs("sample.md"), storage_key=key)
    assert permanent(lambda: parse_version(h.deps, ref)) == NOT_FOUND
    assert h.version(ref).state == "PARSING" and h.audit() == []


class FlakyStorage:
    """Storage whose derived writes fail: an infrastructure error, not the file's fault."""

    def __init__(self, inner: StorageBackend) -> None:
        self.inner = inner

    def put(
        self, data: BinaryIO | bytes, scope: StorageScope, kind: Kind, *a: Any, **kw: Any
    ) -> StoredObject:
        if kind is Kind.DERIVED:
            raise OSError("storage is down")
        return self.inner.put(data, scope, kind, *a, **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def test_e4_5_an_infrastructure_error_is_raised_for_a_retry_and_changes_nothing(
    root: Path,
) -> None:
    h = Harness(root)
    ref = h.add("rfq.md", docs("sample.md"))
    flaky = Harness.__new__(Harness)
    flaky.sessions, flaky.storage = h.sessions, FlakyStorage(h.storage)
    flaky.deps = ParseDeps(h.sessions, flaky.storage)
    with pytest.raises(OSError):
        parse_version(flaky.deps, ref)
    assert h.version(ref).state == "PARSING" and h.audit() == []
    assert parse_version(h.deps, h.ref(ref.ref_id)).parsed  # the next attempt succeeds


# --- giving up ------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", [ErrorCode.TIMEOUT, ErrorCode.PARSE_ERROR])
def test_e4_5_when_the_workflow_gives_up_the_version_is_parse_failed(
    h: Harness, code: ErrorCode
) -> None:
    ref = h.add("rfq.md", docs("sample.md"))
    outcome = mark_failed(h.deps, ParseFailure(ref=ref, code=code))
    assert (outcome.parsed, outcome.code) == (False, code)
    assert (h.version(ref).state, h.version(ref).parse_code) == ("PARSE_FAILED", code.value)
    again = mark_failed(h.deps, ParseFailure(ref=h.ref(ref.ref_id), code=code))
    assert again == outcome and h.outbox().count("document_version.parse_failed") == 1


def test_e4_5_giving_up_never_overwrites_a_parse_that_finished(h: Harness) -> None:
    ref = h.add("rfq.md", docs("sample.md"))
    assert parse_version(h.deps, ref).parsed
    outcome = mark_failed(h.deps, ParseFailure(ref=h.ref(ref.ref_id), code=ErrorCode.TIMEOUT))
    assert outcome.parsed and h.version(ref).state == "PARSED"


@pytest.mark.parametrize(
    ("name", "kind", "fixture", "expect"),
    [
        ("rule.json", "json", "sample.json", "rule: RULE-BOM"),
        ("bom.xml", "xml", "sample.xml", "bom.@project: NAV-9"),
        ("spec.yaml", "yaml", "openapi.yaml", "paths./quotes.get.summary: List quotes"),
        ("cost.py", "py", "sample.py", "- function `extended_cost` (line 4)"),
        ("rfq.eml", "eml", "with_attachment.eml", "# RFQ ADX-2026-014 with scanned drawing"),
        ("rfq.msg", "msg", "with_attachment.msg", "- **From:** RFQ Desk <rfq-desk@example.com>"),
    ],
)
def test_e4_6_a_structured_code_or_mail_file_is_parsed_and_stored(
    h: Harness, name: str, kind: str, fixture: str, expect: str
) -> None:
    ref = h.add(name, docs(fixture), detected=kind)
    outcome = parse_version(h.deps, ref)
    assert outcome.parsed and outcome.code is None
    assert h.version(ref).state == "PARSED"
    md, doc = derived(h, ref)
    assert expect in md.decode() and doc.blocks
