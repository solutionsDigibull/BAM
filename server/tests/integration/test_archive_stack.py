"""E4.7 against the Compose stack: a zip or tar.gz goes through tusd, ClamAV, the type check and the
parse step of the real ingest worker, and its members become child documents that are scanned and
parsed on their own (child workflows of Temporal).

The members are small text, CSV and text PDF files, so no model is involved and the provider mode
of Bifrost does not matter.
"""

import io
import time
import uuid
import zipfile
from collections.abc import Iterator

import pytest
from fixtures import eicar_bytes
from pdfs import make_pdf
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_contracts.documents import ParseReport
from sqlalchemy import select

from .conftest import Compose
from .test_attachment_stack import settled
from .test_ocr_stack import finished
from .test_scan_stack import listing, upload
from .test_uploads_stack import Scope

pytestmark = pytest.mark.integration


@pytest.fixture
def scope() -> Iterator[Scope]:
    s = Scope()
    try:
        yield s
    finally:
        s.drop()


def make_zip(members: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return out.getvalue()


def report(version: DocumentVersion) -> ParseReport:
    assert version.parse_report is not None, (version.state, version.parse_code)
    return ParseReport.model_validate(version.parse_report)


def children(scope: Scope, parent_id: uuid.UUID) -> dict[str, tuple[DocumentVersion, str | None]]:
    """Title -> (version, archive_path) of the documents made from the archive's members."""
    found: dict[str, tuple[DocumentVersion, str | None]] = {}
    with scope.sessions() as s:
        parent = s.scalars(select(DocumentVersion).where(DocumentVersion.id == parent_id)).one()
        rows = s.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.parent_document_id == parent.document_id)
        ).all()
        for document, version in rows:
            found[document.title] = (version, document.archive_path)
    return found


def test_e4_7_t01_the_members_of_a_zip_are_scanned_and_parsed_children(scope: Scope) -> None:
    members = {
        "offer/a.pdf": make_pdf(["Offer for the main board, revision B"]),
        "offer/b.csv": b"MPN,Qty\nSTM32,12\nRC0603,85\n",
        "c.md": b"# Notes\n\nPlease quote by Friday.\n",
    }
    parent_id = upload(scope, "offer.zip", make_zip(members))
    settled(scope, parent_id, children=3)
    versions = {v.id: v for v in scope.versions()}
    parent = versions[parent_id]
    assert parent.state == "PARSED" and parent.detected_type == "zip"
    found = children(scope, parent_id)
    assert sorted(found) == ["a.pdf", "b.csv", "c.md"]
    assert {path for _, path in found.values()} == {"offer/a.pdf", "offer/b.csv", "c.md"}
    for version, _ in found.values():
        assert version.state == "PARSED" and version.scan_status == "CLEAN"  # 3 child docs PARSED
        assert version.scanned_at is not None and version.derived_json_key
    archive = report(parent).archive
    assert archive is not None and (archive.members_total, archive.accepted) == (3, 3)
    assert archive.rejected == [] and report(parent).warnings == []


def test_e4_7_unsafe_nested_and_unsupported_members_are_listed_and_the_rest_are_taken(
    scope: Scope,
) -> None:
    parent_id = upload(
        scope,
        "mixed.zip",
        make_zip(
            {
                "../../x.md": b"evil",
                "inner.zip": make_zip({"deep.md": b"deep"}),
                "setup.exe": b"MZ",
                "ok.md": b"# ok\n\nthe one member that is taken\n",
            }
        ),
    )
    settled(scope, parent_id, children=1)
    parent = next(v for v in scope.versions() if v.id == parent_id)
    assert parent.state == "PARSED"
    archive = report(parent).archive
    assert archive is not None and (archive.members_total, archive.accepted) == (4, 1)
    assert [(m.path, m.code.value) for m in archive.rejected] == [
        ("../../x.md", "PATH_UNSAFE"),
        ("inner.zip", "NESTED_ARCHIVE"),
        ("setup.exe", "UNSUPPORTED_TYPE"),
    ]
    assert sorted(report(parent).warnings) == [
        "Member '../../x.md' was not taken: PATH_UNSAFE",
        "Member 'inner.zip' was not taken: NESTED_ARCHIVE",
        "Member 'setup.exe' was not taken: UNSUPPORTED_TYPE",
    ]


def test_e4_7_t06_a_zip_carrying_eicar_is_rejected_whole_and_nothing_is_taken(
    scope: Scope, compose: Compose
) -> None:
    """ClamAV opens the zip, so the archive's own scan catches the member: the archive is REJECTED
    (MALWARE) and quarantined before any parse, with no child. A member that is rejected on its own
    while the archive stays PARSED is covered in tests/unit/."""
    members = {"ok.md": b"# ok\n", "eicar.txt": eicar_bytes()}
    parent_id = upload(scope, "offer.zip", make_zip(members))
    version = finished(scope, parent_id)
    assert version.state == "REJECTED" and version.reject_code == "MALWARE"
    assert version.scan_status == "INFECTED" and "/quarantine/" in version.storage_key
    assert version.sha256 in listing(compose, scope, "quarantine")
    time.sleep(10)  # a child would have been made by now
    assert [v.id for v in scope.versions()] == [parent_id]


def test_e4_7_t02_a_zip_bomb_is_parse_failed_with_archive_limit(scope: Scope) -> None:
    bomb = make_zip({"zeros.md": b"\0" * (150 * 1024 * 1024)})
    assert len(bomb) < 1024 * 1024
    parent_id = upload(scope, "bomb.zip", bomb)
    version = finished(scope, parent_id)
    assert version.state in ("PARSE_FAILED", "REJECTED"), (version.state, version.reject_code)
    if version.state == "PARSE_FAILED":  # the scan let it through: the loader stops it
        assert version.parse_code == "ARCHIVE_LIMIT" and version.derived_json_key is None
    assert [v.id for v in scope.versions()] == [parent_id]  # nothing was unpacked into documents
