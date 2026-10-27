"""E4.6 attachments of an e-mail: `register_attachments` and `finish_attachments` on SQLite and a
temp-dir storage (the stack is covered in tests/integration/test_attachment_stack.py)."""

import uuid
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from fixtures import fixture_path
from pdfs import make_pdf
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_contracts.documents import AttachmentOutcome, ParseReport
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest.attachments import (
    NOT_FOUND,
    AttachmentDeps,
    finish_attachments_for,
    register_attachments_for,
    safe_name,
)
from sbam_workers.ingest.parsing import parse_version
from sqlalchemy import select
from temporalio.exceptions import ApplicationError
from test_parsing import Harness, root  # noqa: F401 - root: the short-path fixture


@pytest.fixture
def h(root: Path) -> Harness:  # noqa: F811 - the imported fixture
    return Harness(root)


def mail(*attachments: tuple[str, bytes]) -> bytes:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = "a@example.com", "b@example.com", "RFQ"
    message.set_content("see attached")
    for name, data in attachments:
        message.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return message.as_bytes()


def parsed_mail(h: Harness, data: bytes, **fields: Any) -> StageRef:
    ref = h.add("rfq.eml", data, detected="eml", **fields)
    assert parse_version(h.deps, ref).parsed
    return ref


def deps(h: Harness) -> AttachmentDeps:
    return AttachmentDeps(h.sessions, h.storage)


def children(h: Harness, parent: StageRef) -> list[tuple[Document, DocumentVersion]]:
    with h.sessions() as s:
        parent_doc = s.get(DocumentVersion, parent.ref_id).document_id  # type: ignore[union-attr]
        rows = s.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.parent_document_id == parent_doc)
            .order_by(DocumentVersion.id)
        ).all()
        for doc, version in rows:
            s.expunge(doc)
            s.expunge(version)
        return [(d, v) for d, v in rows]


def report(h: Harness, ref: StageRef) -> ParseReport:
    raw = h.version(ref).parse_report
    assert raw is not None
    return ParseReport.model_validate(raw)


def fill_project(h: Harness, count: int, state: str = "PARSED") -> None:
    for n in range(count):
        h.add(f"other-{n}.md", f"text {n}".encode(), state=state)


def test_e4_6_t01_an_attachment_becomes_a_child_document_linked_to_its_parent(h: Harness) -> None:
    pdf = make_pdf(["spec"])
    parent = parsed_mail(h, mail(("spec.pdf", pdf)))
    ids = register_attachments_for(deps(h), parent).ids
    [(doc, child)] = children(h, parent)
    assert ids == [child.id] and doc.title == "spec.pdf"
    assert child.state == "UPLOADED" and child.size == len(pdf) and child.scan_status is None
    assert child.mime == "application/pdf" and h.storage.get(child.storage_key) == pdf
    [item] = report(h, parent).attachments
    assert item.outcome is AttachmentOutcome.REGISTERED and item.document_id == doc.id
    assert item.name == "spec.pdf" and item.size == len(pdf)
    assert h.version(parent).state == "PARSED"  # the e-mail stays PARSED
    assert "DOCUMENT_ATTACHMENT_ADDED" in h.audit()


def test_e4_6_t01_the_fixture_email_has_the_rfq_scan_as_its_child(h: Harness) -> None:
    parent = parsed_mail(h, fixture_path("documents", "with_attachment.eml").read_bytes())
    register_attachments_for(deps(h), parent)
    [(doc, child)] = children(h, parent)
    assert doc.title == "rfq_scan.pdf"
    assert (
        h.storage.get(child.storage_key)
        == fixture_path("documents", "scanned_full.pdf").read_bytes()
    )


def test_e4_6_registering_twice_makes_the_children_once(h: Harness) -> None:
    parent = parsed_mail(h, mail(("a.pdf", make_pdf(["a"])), ("b.pdf", make_pdf(["b"]))))
    first = register_attachments_for(deps(h), parent).ids
    assert register_attachments_for(deps(h), parent).ids == first  # an activity retry
    assert len(children(h, parent)) == 2 and len(first) == 2
    assert [a.outcome for a in report(h, parent).attachments] == [AttachmentOutcome.REGISTERED] * 2


def test_e4_6_the_same_file_twice_in_one_email_makes_two_children(h: Harness) -> None:
    pdf = make_pdf(["same"])
    parent = parsed_mail(h, mail(("one.pdf", pdf), ("two.pdf", pdf)))
    assert len(set(register_attachments_for(deps(h), parent).ids)) == 2


def test_e4_6_t08_attachments_beyond_the_20_file_limit_are_recorded_not_added(h: Harness) -> None:
    fill_project(h, 18)  # 18 other active documents
    parent = parsed_mail(h, mail(("a.pdf", make_pdf(["a"])), ("b.pdf", make_pdf(["b"]))))
    ids = register_attachments_for(deps(h), parent).ids  # the e-mail is the 19th
    assert len(ids) == 1
    outcomes = [a.outcome for a in report(h, parent).attachments]
    assert outcomes == [AttachmentOutcome.REGISTERED, AttachmentOutcome.FILE_LIMIT_REACHED]
    assert report(h, parent).attachments[1].document_id is None
    assert h.version(parent).state == "PARSED" and len(children(h, parent)) == 1
    finish_attachments_for(deps(h), parent)
    warnings = report(h, parent).warnings
    assert warnings == ["Attachment 'b.pdf' was not added: the project already holds 20 files"]


def test_e4_6_rejected_and_deleted_documents_do_not_use_up_the_limit(h: Harness) -> None:
    fill_project(h, 10)
    fill_project(h, 8, state="REJECTED")  # none of these counts
    parent = parsed_mail(h, mail(("a.pdf", make_pdf(["a"])), ("b.pdf", make_pdf(["b"]))))
    assert len(register_attachments_for(deps(h), parent).ids) == 2


def test_e4_6_an_attachment_of_an_attachment_is_not_followed(h: Harness) -> None:
    top = parsed_mail(h, mail(("inner.eml", mail(("deep.pdf", make_pdf(["deep"]))))))
    register_attachments_for(deps(h), top)
    [(inner_doc, inner)] = children(h, top)
    assert inner_doc.parent_document_id is not None
    # the nested e-mail is ingested like any child; here scan and detection are done by hand
    set_child(h, inner.id, state="PARSING", scan_status="CLEAN", detected_type="eml")
    set_child(h, inner.id, filetype_row_label="Filetype #6")
    ref = Harness.ref(inner.id)
    assert parse_version(h.deps, ref).parsed
    assert register_attachments_for(deps(h), ref).ids == []  # one level only
    [item] = report(h, ref).attachments
    assert item.outcome is AttachmentOutcome.TOO_DEEP and item.document_id is None
    assert children(h, ref) == []
    finish_attachments_for(deps(h), ref)
    assert report(h, ref).warnings == [
        "Attachment 'deep.pdf' was not added: only one level of attachments is followed"
    ]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\a\\rfq.pdf", "rfq.pdf"),
        ("bad\x00name\x07.pdf", "badname.pdf"),
        ("", "attachment-1"),
        ("..", "attachment-1"),
        ("x" * 400 + ".pdf", ("x" * 400 + ".pdf")[:255]),
    ],
)
def test_e4_6_an_attachment_name_has_no_path_or_control_characters(
    given: str, expected: str
) -> None:
    assert safe_name(given, 0) == expected


def test_e4_6_an_email_with_no_attachment_makes_no_child(h: Harness) -> None:
    parent = parsed_mail(h, mail())
    assert register_attachments_for(deps(h), parent).ids == []
    assert children(h, parent) == [] and report(h, parent).attachments == []


def test_e4_6_a_version_that_is_not_a_parsed_email_is_left_alone(h: Harness) -> None:
    note = h.add("note.md", b"# hi")
    assert parse_version(h.deps, note).parsed
    assert register_attachments_for(deps(h), note).ids == []
    unparsed = h.add("rfq.eml", mail(("a.pdf", make_pdf(["a"]))), detected="eml")
    assert register_attachments_for(deps(h), unparsed).ids == []  # still PARSING


def test_e4_6_another_tenants_version_is_not_found(h: Harness) -> None:
    parent = parsed_mail(h, mail(("a.pdf", make_pdf(["a"]))))
    stranger = Harness.ref(parent.ref_id, tenant=uuid.uuid4())
    with pytest.raises(ApplicationError) as caught:
        register_attachments_for(deps(h), stranger)
    assert caught.value.type == NOT_FOUND and caught.value.non_retryable


def set_child(h: Harness, version_id: uuid.UUID, **fields: Any) -> None:
    with h.sessions() as s:
        row = s.get(DocumentVersion, version_id)
        assert row is not None
        for key, value in fields.items():
            setattr(row, key, value)
        s.commit()


def test_e4_6_t03_an_infected_attachment_warns_on_the_parent_which_stays_parsed(h: Harness) -> None:
    parent = parsed_mail(h, mail(("ok.pdf", make_pdf(["ok"])), ("bad.pdf", make_pdf(["bad"]))))
    ok, bad = register_attachments_for(deps(h), parent).ids
    set_child(h, ok, state="PARSED")
    set_child(h, bad, state="REJECTED", reject_code="MALWARE")
    assert finish_attachments_for(deps(h), parent).ids == [ok, bad]
    assert h.version(parent).state == "PARSED"
    assert report(h, parent).warnings == ["Attachment 'bad.pdf' was rejected: MALWARE"]


def test_e4_6_unreadable_and_unscanned_attachments_are_noted(h: Harness) -> None:
    parent = parsed_mail(h, mail(("a.pdf", make_pdf(["a"])), ("b.pdf", make_pdf(["b"]))))
    a, b = register_attachments_for(deps(h), parent).ids
    set_child(h, a, state="PARSE_FAILED", parse_code="ENCRYPTED")
    set_child(h, b, state="SCAN_FAILED")
    finish_attachments_for(deps(h), parent)
    assert report(h, parent).warnings == [
        "Attachment 'a.pdf' could not be read: ENCRYPTED",
        "Attachment 'b.pdf' could not be scanned",
    ]


def test_e4_6_finishing_twice_does_not_repeat_a_warning_and_keeps_the_emails_own(
    h: Harness,
) -> None:
    parent = parsed_mail(h, mail(("a.pdf", make_pdf(["a"]))))
    [a] = register_attachments_for(deps(h), parent).ids
    set_child(h, a, state="REJECTED", reject_code="UNSUPPORTED_TYPE")
    with h.sessions() as s:
        row = s.get(DocumentVersion, parent.ref_id)
        assert row is not None
        row.parse_report = {**(row.parse_report or {}), "warnings": ["the e-mail's own warning"]}
        s.commit()
    finish_attachments_for(deps(h), parent)
    finish_attachments_for(deps(h), parent)
    assert report(h, parent).warnings == [
        "the e-mail's own warning",
        "Attachment 'a.pdf' was rejected: UNSUPPORTED_TYPE",
    ]


def test_e4_6_finish_leaves_children_that_went_fine_without_a_warning(h: Harness) -> None:
    parent = parsed_mail(h, mail(("a.pdf", make_pdf(["a"]))))
    [a] = register_attachments_for(deps(h), parent).ids
    set_child(h, a, state="PARSED")
    finish_attachments_for(deps(h), parent)
    assert report(h, parent).warnings == []
