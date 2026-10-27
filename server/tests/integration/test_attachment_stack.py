"""E4.6 against the Compose stack: an e-mail goes through tusd, ClamAV, the type check and the parse
step of the real ingest worker, and its attachments become child documents that are scanned and
parsed on their own (child workflows of Temporal).

The attachments are small text PDFs, so no model is involved and the provider mode of Bifrost does
not matter.
"""

import asyncio
import time
import uuid
from email.message import EmailMessage

import pytest
from fixtures import eicar_bytes
from pdfs import make_pdf
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.temporal.client import connect
from sbam_contracts.documents import AttachmentOutcome, ParseReport
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError

from .conftest import Compose
from .test_ocr_stack import WAIT, finished
from .test_scan_stack import listing, upload
from .test_uploads_stack import TEMPORAL, Scope

pytestmark = pytest.mark.integration


@pytest.fixture
def scope():  # type: ignore[no-untyped-def]
    s = Scope()
    try:
        yield s
    finally:
        s.drop()


def mail(*attachments: tuple[str, bytes]) -> bytes:
    message = EmailMessage()
    message["From"], message["To"] = "rfq-desk@example.com", "sourcing@example.com"
    message["Subject"] = "RFQ with attachments"
    message.set_content("please find the files attached")
    for name, data in attachments:
        message.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return message.as_bytes()


def report(version: DocumentVersion) -> ParseReport:
    assert version.parse_report is not None, (version.state, version.parse_code)
    return ParseReport.model_validate(version.parse_report)


async def closed(version_id: uuid.UUID) -> bool:
    """The e-mail's `DocumentIngestion` has ended, so its children have too and
    `finish_attachments` has written its warnings."""
    client = await connect(TEMPORAL, "sbam")
    description = await client.get_workflow_handle(f"ingest-{version_id}").describe()
    return description.status != WorkflowExecutionStatus.RUNNING


def settled(scope: Scope, parent_id: uuid.UUID, children: int, seconds: int = WAIT) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if len(scope.versions()) == children + 1 and asyncio.run(closed(parent_id)):
                return
        except RPCError:  # the workflow is not started yet
            pass
        time.sleep(2)
    raise AssertionError(f"not settled: {[(v.state, v.reject_code) for v in scope.versions()]}")


def parent_of(scope: Scope, version: DocumentVersion) -> uuid.UUID | None:
    with scope.sessions() as s:
        document = s.get(Document, version.document_id)
        assert document is not None
        return document.parent_document_id


def test_e4_6_t01_an_attachment_is_a_scanned_and_parsed_child_of_the_email(scope: Scope) -> None:
    pdf = make_pdf(["Spec sheet for the main board, revision B"])
    parent_id = upload(scope, "rfq.eml", mail(("spec.pdf", pdf)))
    settled(scope, parent_id, children=1)
    versions = {v.id: v for v in scope.versions()}
    parent = versions.pop(parent_id)
    [child] = versions.values()
    assert parent.state == "PARSED" and child.state == "PARSED"  # 2 documents PARSED
    assert child.scan_status == "CLEAN" and child.scanned_at is not None  # the child was scanned
    assert parent_of(scope, parent) is None and parent_of(scope, child) == parent.document_id
    [item] = report(parent).attachments
    assert item.outcome is AttachmentOutcome.REGISTERED and item.name == "spec.pdf"
    assert item.document_id == child.document_id and item.size == len(pdf)
    assert report(parent).warnings == []
    assert child.detected_type == "pdf" and child.derived_json_key


def test_e4_6_t03_an_email_carrying_eicar_is_rejected_whole_and_nothing_is_parsed(
    scope: Scope, compose: Compose
) -> None:
    """ClamAV decodes the MIME attachment, so the infected message is caught by the e-mail's own
    scan: it is REJECTED (MALWARE) and quarantined before any parse, and no child is made. A child
    that is rejected on its own, with the e-mail still PARSED, is covered in tests/unit/."""
    ok = make_pdf(["A harmless attachment of the offer"])
    parent_id = upload(scope, "offer.eml", mail(("ok.pdf", ok), ("eicar.com.txt", eicar_bytes())))
    version = finished(scope, parent_id)
    assert version.state == "REJECTED" and version.reject_code == "MALWARE"
    assert version.scan_status == "INFECTED" and "/quarantine/" in version.storage_key
    assert version.sha256 in listing(compose, scope, "quarantine")
    assert version.parse_report is None and version.derived_json_key is None
    time.sleep(10)  # a child would have been made by now
    assert [v.id for v in scope.versions()] == [parent_id]
