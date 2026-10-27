"""E3.8b `parse_master_document`: a master document is parsed into `doc.md` and `doc.json` on SQLite
and a temp-dir storage (the stack is covered in tests/integration/test_master_document_stack.py)."""

import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest
from fixtures import fixture_path
from pdfs import make_pdf
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.core.ids import uuid7
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.masters.models import MasterDocument
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import ParsedDocument, ParseReport
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest.master_documents import (
    NOT_FOUND,
    NOT_SCANNED,
    MasterDeps,
    mark_failed_for,
    parse_master_document_for,
)
from sbam_workers.ingest.parsing import ParseFailure
from sqlalchemy import Engine, pool, select
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from test_parsing import root  # noqa: F401 - the short-path fixture

TENANT, PROJECT, ZERO = uuid.uuid4(), uuid.uuid4(), uuid.UUID(int=0)


def sqlite_sessions() -> sessionmaker[Session]:
    engine: Engine = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    for table in (MasterDocument, AuditLog, NotificationOutbox):
        table.__table__.create(engine)  # type: ignore[attr-defined]
    return make_sessionmaker(engine)


class Harness:
    def __init__(self, base: Path) -> None:
        self.sessions = sqlite_sessions()
        self.storage = LocalDiskBackend(base)
        self.deps = MasterDeps(self.sessions, self.storage)

    def add(
        self,
        name: str,
        data: bytes,
        *,
        tenant: uuid.UUID = TENANT,
        project: uuid.UUID | None = PROJECT,
        scan: str | None = "CLEAN",
        parse: str = "PENDING",
    ) -> StageRef:
        scope = StorageScope(tenant, project or ZERO)
        stored = self.storage.put(data, scope, Kind.UPLOADS, "x/y")
        with self.sessions() as s:
            row = MasterDocument(
                tenant_id=tenant,
                project_id=project,
                title=name,
                filetype_row_label="Filetype #12",
                intent_class="Policy / rules",
                master_type="RULES",
                route_source="DEFAULT_BY_FILETYPE",
                storage_key=stored.storage_key,
                sha256=stored.sha256,
                size=stored.size,
                mime="text/plain",
                scan_status=scan,
                parse_status=parse,
            )
            s.add(row)
            s.commit()
            return self.ref(row.id, tenant, project or ZERO)

    @staticmethod
    def ref(
        document_id: uuid.UUID, tenant: uuid.UUID = TENANT, project: uuid.UUID = PROJECT
    ) -> StageRef:
        return StageRef(
            tenant_id=tenant,
            project_id=project,
            run_id=uuid7(),
            correlation_id="e3-8b-test",
            ref_id=document_id,
        )

    def row(self, ref: StageRef) -> MasterDocument:
        with self.sessions() as s:
            row = s.get(MasterDocument, ref.ref_id)
            assert row is not None
            s.expunge(row)
            return row

    def audit(self) -> list[str]:
        with self.sessions() as s:
            return [r.action for r in s.scalars(select(AuditLog).order_by(AuditLog.id))]

    def derived(self, ref: StageRef) -> tuple[str, ParsedDocument]:
        row = self.row(ref)
        assert row.derived_md_key and row.derived_json_key
        md = self.storage.get(row.derived_md_key).decode()
        return md, ParsedDocument.model_validate(json.loads(self.storage.get(row.derived_json_key)))


@pytest.fixture
def h(root: Path) -> Harness:  # noqa: F811 - the imported fixture
    return Harness(root)


def docs(name: str) -> bytes:
    return fixture_path("documents", name).read_bytes()


def report(h: Harness, ref: StageRef) -> ParseReport:
    raw = h.row(ref).parse_report
    assert raw is not None
    return ParseReport.model_validate(raw)


# --- parsed ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fixture", "expect"),
    [
        ("rules.md", "sample.md", "# NAV-9 RFQ summary"),
        ("rules.yaml", "sample.yaml", "rule: RULE-OFFER"),
        ("agent.json", "fastmcp_spec.json", "tools[0].name: scrub_bom"),
        ("parts.csv", "sample.csv", "|"),
        ("rates.xlsx", "multi_sheet.xlsx", ""),
        ("terms.docx", "sample.docx", ""),
    ],
)
def test_e3_8b_a_master_document_is_parsed_and_its_derivatives_are_stored(
    h: Harness, name: str, fixture: str, expect: str
) -> None:
    ref = h.add(name, docs(fixture))
    assert parse_master_document_for(h.deps, ref).ids == [ref.ref_id]
    row = h.row(ref)
    assert row.parse_status == "PARSED" and row.parse_code is None
    md, doc = h.derived(ref)
    assert expect in md and doc.document_version_id == ref.ref_id and doc.blocks
    assert row.derived_md_key.startswith(f"{TENANT}/{PROJECT}/derived/")  # type: ignore[union-attr]
    assert report(h, ref).tables >= 0 and h.audit() == ["MASTER_DOCUMENT_PARSED"]


def test_e3_8b_a_text_pdf_is_parsed_with_a_block_per_page(h: Harness) -> None:
    ref = h.add(
        "costing.pdf",
        make_pdf([f"Costing standard line {n} with the parts and the prices" for n in (1, 2)]),
    )
    assert parse_master_document_for(h.deps, ref).ids == [ref.ref_id]
    _, doc = h.derived(ref)
    assert [b.page for b in doc.blocks] == [1, 2]


def test_e3_8b_a_tenant_wide_document_is_stored_under_the_tenant_scope(h: Harness) -> None:
    ref = h.add("rules.md", docs("sample.md"), project=None)
    assert parse_master_document_for(h.deps, ref).ids == [ref.ref_id]
    assert h.row(ref).derived_md_key.startswith(f"{TENANT}/{ZERO}/derived/")  # type: ignore[union-attr]


def test_e3_8b_parsing_twice_gives_the_same_answer_and_one_audit_row(h: Harness) -> None:
    ref = h.add("rules.md", docs("sample.md"))
    first = parse_master_document_for(h.deps, ref).ids
    key = h.row(ref).derived_md_key
    assert parse_master_document_for(h.deps, ref).ids == first  # an activity retry
    assert h.row(ref).derived_md_key == key and h.audit() == ["MASTER_DOCUMENT_PARSED"]


def test_e3_8b_a_document_left_in_parsing_by_a_dead_worker_is_parsed_on_the_retry(
    h: Harness,
) -> None:
    ref = h.add("rules.md", docs("sample.md"), parse="PARSING")
    assert parse_master_document_for(h.deps, ref).ids == [ref.ref_id]
    assert h.row(ref).parse_status == "PARSED"


def test_e3_8b_an_email_and_an_archive_are_listed_not_expanded(h: Harness) -> None:
    mail = h.add("rfq.eml", docs("with_attachment.eml"))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("a.md", "# a")
        archive.writestr("b.md", "# b")
    pack = h.add("pack.zip", out.getvalue())
    for ref in (mail, pack):
        assert parse_master_document_for(h.deps, ref).ids == [ref.ref_id]
    md, _ = h.derived(pack)
    assert "- a.md" in md and "- b.md" in md
    with h.sessions() as s:  # no child documents exist: a master document is one file
        assert len(list(s.scalars(select(MasterDocument)))) == 2


# --- failed ---------------------------------------------------------------------------------------


def test_e3_8b_a_file_that_is_not_what_its_name_says_is_failed_type_mismatch(h: Harness) -> None:
    ref = h.add("rules.pdf", docs("scan.png"))
    assert parse_master_document_for(h.deps, ref).ids == []
    row = h.row(ref)
    assert row.parse_status == "FAILED" and row.parse_code == "TYPE_MISMATCH"
    assert row.derived_md_key is None and h.audit() == ["MASTER_DOCUMENT_PARSE_FAILED"]


def test_e3_8b_text_that_is_not_utf8_is_failed_invalid_encoding(h: Harness) -> None:
    ref = h.add("rules.md", b"caf\xe9 \xff\xfe not utf-8")
    assert parse_master_document_for(h.deps, ref).ids == []
    assert h.row(ref).parse_code == ErrorCode.INVALID_ENCODING.value


def test_e3_8b_a_corrupt_file_is_failed_with_the_loaders_code(h: Harness) -> None:
    ref = h.add("rates.json", b'{"a": ')
    assert parse_master_document_for(h.deps, ref).ids == []
    row = h.row(ref)
    assert (row.parse_status, row.parse_code) == ("FAILED", "CORRUPT")
    assert report(h, ref).warnings


def test_e3_8b_a_scanned_pdf_is_failed_empty_not_sent_to_the_vision_model(h: Harness) -> None:
    ref = h.add("costing.pdf", docs("scanned_full.pdf"))
    assert parse_master_document_for(h.deps, ref).ids == []
    row = h.row(ref)
    assert (row.parse_status, row.parse_code) == ("FAILED", "EMPTY")
    assert "vision model" in report(h, ref).warnings[0]


def test_e3_8b_an_empty_archive_is_failed_empty(h: Harness) -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("setup.exe", "MZ")
    ref = h.add("pack.zip", out.getvalue())
    assert parse_master_document_for(h.deps, ref).ids == []
    assert h.row(ref).parse_code == "EMPTY"


def test_e3_8b_a_document_that_is_failed_stays_failed(h: Harness) -> None:
    ref = h.add("rules.pdf", docs("scan.png"))
    parse_master_document_for(h.deps, ref)
    assert parse_master_document_for(h.deps, ref).ids == []
    assert h.audit() == ["MASTER_DOCUMENT_PARSE_FAILED"]  # not written twice


# --- the guards -----------------------------------------------------------------------------------


@pytest.mark.parametrize("scan", [None, "INFECTED", "SCAN_FAILED"])
def test_e3_8b_nothing_is_read_unless_the_scan_was_clean(h: Harness, scan: str | None) -> None:
    ref = h.add("rules.md", docs("sample.md"), scan=scan)
    with pytest.raises(ApplicationError) as caught:
        parse_master_document_for(h.deps, ref)
    assert caught.value.type == NOT_SCANNED and caught.value.non_retryable
    assert h.row(ref).parse_status == "PENDING" and h.row(ref).derived_md_key is None


def test_e3_8b_a_missing_document_is_not_found_and_not_retried(h: Harness) -> None:
    with pytest.raises(ApplicationError) as caught:
        parse_master_document_for(h.deps, Harness.ref(uuid.uuid4()))
    assert caught.value.type == NOT_FOUND and caught.value.non_retryable


def test_e3_8b_another_tenants_document_is_not_found(h: Harness) -> None:
    ref = h.add("rules.md", docs("sample.md"))
    stranger = Harness.ref(ref.ref_id, tenant=uuid.uuid4())
    with pytest.raises(ApplicationError) as caught:
        parse_master_document_for(h.deps, stranger)
    assert caught.value.type == NOT_FOUND
    assert h.row(ref).parse_status == "PENDING"  # untouched


def test_e3_8b_a_missing_stored_file_is_not_found(h: Harness) -> None:
    ref = h.add("rules.md", docs("sample.md"))
    with h.sessions() as s:
        row = s.get(MasterDocument, ref.ref_id)
        assert row is not None
        row.storage_key = f"{TENANT}/{PROJECT}/uploads/{'0' * 64}"
        s.commit()
    with pytest.raises(ApplicationError) as caught:
        parse_master_document_for(h.deps, ref)
    assert caught.value.type == NOT_FOUND and caught.value.non_retryable


# --- giving up ------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", [ErrorCode.PARSE_ERROR, ErrorCode.TIMEOUT])
def test_e3_8b_when_the_workflow_gives_up_the_document_is_failed(
    h: Harness, code: ErrorCode
) -> None:
    ref = h.add("rules.md", docs("sample.md"), parse="PARSING")
    assert mark_failed_for(h.deps, ParseFailure(ref=ref, code=code)).ids == []
    row = h.row(ref)
    assert (row.parse_status, row.parse_code) == ("FAILED", code.value)


def test_e3_8b_giving_up_keeps_an_answer_that_was_already_stored(h: Harness) -> None:
    ref = h.add("rules.md", docs("sample.md"))
    parse_master_document_for(h.deps, ref)
    out = mark_failed_for(h.deps, ParseFailure(ref=ref, code=ErrorCode.TIMEOUT))
    assert out.ids == [ref.ref_id] and h.row(ref).parse_status == "PARSED"
