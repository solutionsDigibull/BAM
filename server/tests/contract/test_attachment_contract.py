"""E4.6 contract tests: the attachments of a parsed e-mail and the parent of a document."""

import uuid

import pytest
from pydantic import ValidationError
from sbam_contracts.documents import (
    MAX_ATTACHMENT_DEPTH,
    AttachmentOutcome,
    AttachmentReport,
    DocumentOut,
    ParseReport,
)


def attachment(**changes: object) -> dict[str, object]:
    return {
        "name": "rfq_scan.pdf",
        "content_type": "application/pdf",
        "size": 36280,
        "outcome": "REGISTERED",
        **changes,
    }


def test_e4_6_only_one_level_of_attachments_is_followed() -> None:
    assert MAX_ATTACHMENT_DEPTH == 1


def test_e4_6_a_report_written_before_e4_6_still_validates_with_no_attachments() -> None:
    old = {
        "pages": 2,
        "sheets": 0,
        "tables": 0,
        "warnings": [],
        "ocr_needed_pages": [],
        "ocr_pages": [],
        "unreadable_pages": [],
    }
    assert ParseReport.model_validate(old).attachments == []


def test_e4_6_a_report_lists_each_attachment_with_what_became_of_it() -> None:
    child = uuid.uuid4()
    report = ParseReport.model_validate(
        {
            "attachments": [
                attachment(document_id=str(child)),
                attachment(name="b.pdf", outcome="FILE_LIMIT_REACHED"),
                attachment(name="c.pdf", outcome="TOO_LARGE"),
                attachment(name="d.eml", outcome="TOO_DEEP"),
            ]
        }
    )
    assert [a.outcome for a in report.attachments] == list(AttachmentOutcome)
    assert report.attachments[0].document_id == child and report.attachments[1].document_id is None
    assert ParseReport.model_validate(report.model_dump(mode="json")) == report  # round trip


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "ACCEPTED"},
        {"name": ""},
        {"name": "x" * 256},
        {"size": -1},
        {"document_id": "not-a-uuid"},
        {"extra": 1},
    ],
)
def test_e4_6_an_attachment_with_a_bad_field_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AttachmentReport.model_validate(attachment(**changes))


def test_e4_6_a_document_may_name_its_parent_and_an_upload_has_none() -> None:
    base = {
        "id": str(uuid.uuid4()),
        "title": "rfq_scan.pdf",
        "state": "PARSED",
        "size": 1,
        "mime": "application/pdf",
        "sha256": "0" * 64,
        "created_at": "2026-10-23T09:00:00Z",
    }
    assert DocumentOut.model_validate(base).parent_document_id is None
    parent = uuid.uuid4()
    assert (
        DocumentOut.model_validate({**base, "parent_document_id": str(parent)}).parent_document_id
        == parent
    )
