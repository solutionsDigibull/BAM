"""E4.6 loaders for `.eml` and Outlook `.msg`: headers, body, attachments handed back, safety."""

import struct
import uuid
from email.message import EmailMessage

import pytest
from fixtures import fixture_path
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, ParsedDocument
from sbam_workers.ingest.loaders import Parsed, ParseError, loader_for, no_heartbeat
from sbam_workers.ingest.loaders.email import MAX_ATTACHMENT_BYTES


def docs(name: str) -> bytes:
    return fixture_path("documents", name).read_bytes()


def run(kind: str, data: bytes) -> Parsed:
    loader = loader_for(kind)
    assert loader is not None
    return loader(data, no_heartbeat)


def refused(kind: str, data: bytes) -> ErrorCode:
    with pytest.raises(ParseError) as caught:
        run(kind, data)
    return caught.value.code


def blocks_of(parsed: Parsed) -> list[Block]:
    return list(parsed.blocks)


def markdown_of(parsed: Parsed) -> str:
    assert isinstance(parsed.markdown, str)
    return parsed.markdown


def mail(body: str = "Hello", *, subject: str = "Quote", html: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = "Ann <ann@example.com>"
    message["To"] = "bob@example.com, Cy <cy@example.com>"
    message["Date"] = "Thu, 01 Oct 2026 09:30:00 +0000"
    message["Subject"] = subject
    message.set_content(body)
    if html is not None:
        message.add_alternative(html, subtype="html")
    return message


# --- .eml ----------------------------------------------------------------------------------------


def test_e4_6_t01_eml_headers_body_and_the_attachment_are_found() -> None:
    parsed = run("eml", docs("with_attachment.eml"))
    text = markdown_of(parsed)
    assert "# RFQ ADX-2026-014 with scanned drawing" in text
    assert "- **From:** rfq-desk@example.com" in text and "- **To:** sourcing@example.com" in text
    assert "- **Date:** Thu, 01 Oct 2026 09:30:00 +0000" in text
    assert "please find the scanned RFQ attached" in text
    [attachment] = parsed.attachments
    assert attachment.name == "rfq_scan.pdf" and attachment.content_type == "application/pdf"
    assert attachment.data == docs("scanned_full.pdf")  # identical to the standalone fixture
    assert "- rfq_scan.pdf (application/pdf," in text
    ParsedDocument(document_version_id=uuid.uuid4(), loader="email", blocks=blocks_of(parsed))


def test_e4_6_eml_blocks_point_at_their_line_in_doc_md() -> None:
    parsed = run("eml", docs("with_attachment.eml"))
    lines = markdown_of(parsed).splitlines()
    for block in blocks_of(parsed):
        assert block.line is not None
        assert block.text.splitlines()[0] in lines[block.line - 1]


def test_e4_6_eml_html_only_body_is_reduced_to_text_without_scripts() -> None:
    message = EmailMessage()
    message["From"], message["Subject"] = "a@example.com", "Hi"
    message.set_content(
        "<html><body><p>Price <b>12</b></p><script>alert(1)</script>"
        "<img src='http://evil.example/x.png'><style>p{}</style><p>Thanks</p></body></html>",
        subtype="html",
    )
    text = markdown_of(run("eml", message.as_bytes()))
    assert "Price 12" in text and "Thanks" in text
    assert "alert" not in text and "evil.example" not in text and "p{}" not in text


def test_e4_6_eml_prefers_the_plain_part_of_an_alternative() -> None:
    message = mail("plain words", html="<p>html words</p>")
    text = markdown_of(run("eml", message.as_bytes()))
    assert "plain words" in text and "html words" not in text


def test_e4_6_eml_nested_message_is_an_attachment_with_its_own_bytes() -> None:
    inner = mail("inner body", subject="Inner")
    outer = mail("outer body")
    outer.add_attachment(inner)
    [attachment] = run("eml", outer.as_bytes()).attachments
    assert attachment.content_type == "message/rfc822" and attachment.name.endswith(".eml")
    again = run("eml", attachment.data)
    assert "inner body" in markdown_of(again) and "# Inner" in markdown_of(again)


def test_e4_6_eml_inline_image_with_a_filename_is_an_attachment() -> None:
    message = mail("see logo")
    png = b"\x89PNG\r\n\x1a\nxxxx"
    message.add_attachment(png, maintype="image", subtype="png", filename="logo.png")
    [attachment] = run("eml", message.as_bytes()).attachments
    assert attachment.name == "logo.png" and attachment.content_type == "image/png"


def test_e4_6_eml_attachment_over_15_mb_is_left_out_with_a_warning() -> None:
    message = mail()
    message.add_attachment(
        b"0" * (MAX_ATTACHMENT_BYTES + 1), maintype="application", subtype="pdf", filename="big.pdf"
    )
    small = b"%PDF-1.4 small"
    message.add_attachment(small, maintype="application", subtype="pdf", filename="ok.pdf")
    parsed = run("eml", message.as_bytes())
    assert [a.name for a in parsed.attachments] == ["ok.pdf"]
    assert any("big.pdf" in w and "15 MB" in w for w in parsed.report.warnings)


def test_e4_6_eml_attachment_without_a_name_gets_one() -> None:
    message = mail()
    message.add_attachment(b"abc", maintype="application", subtype="octet-stream")
    [attachment] = run("eml", message.as_bytes()).attachments
    assert attachment.name == "attachment-1"


def test_e4_6_eml_header_text_cannot_inject_lines_or_control_characters() -> None:
    raw = b"From: a@example.com\r\nSubject: Hi\x07 there\r\nTo: b@example.com\r\n\r\nbody\r\n"
    text = markdown_of(run("eml", raw))
    assert "\x07" not in text and "# Hi there" in text


@pytest.mark.parametrize("raw", [b"", b"   \r\n", b"\x00\x01\x02 not a mail"])
def test_e4_6_a_file_with_no_headers_and_no_body_is_corrupt(raw: bytes) -> None:
    assert refused("eml", raw) == ErrorCode.CORRUPT


def test_e4_6_eml_with_only_headers_is_still_a_mail() -> None:
    assert "- **Subject:** Only" in markdown_of(run("eml", b"Subject: Only\r\nFrom: a@b.c\r\n\r\n"))


# --- .msg ----------------------------------------------------------------------------------------


def test_e4_6_t02_msg_headers_and_body_are_in_doc_md() -> None:
    parsed = run("msg", docs("with_attachment.msg"))
    text = markdown_of(parsed)
    assert "# RFQ ADX-2026-014 with scanned drawing (Outlook)" in text
    assert "- **From:** RFQ Desk <rfq-desk@example.com>" in text
    assert "sourcing@example.com" in text
    assert "- **Date:**" in text
    [attachment] = parsed.attachments
    assert attachment.name == "rfq_scan.pdf"
    assert attachment.data == docs("scanned_full.pdf")


def test_e4_6_msg_blocks_validate_as_doc_json() -> None:
    parsed = run("msg", docs("with_attachment.msg"))
    ParsedDocument(document_version_id=uuid.uuid4(), loader="email", blocks=blocks_of(parsed))
    assert any(b.type == "paragraph" and "RFQ" in b.text for b in blocks_of(parsed))


@pytest.mark.parametrize(
    "raw",
    [b"", b"not an ole file", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600],
)
def test_e4_6_msg_that_is_not_a_readable_outlook_message_is_corrupt(raw: bytes) -> None:
    assert refused("msg", raw) == ErrorCode.CORRUPT


def test_e4_6_a_truncated_msg_is_corrupt() -> None:
    data = docs("with_attachment.msg")
    assert refused("msg", data[: len(data) // 3]) == ErrorCode.CORRUPT


def test_e4_6_msg_truncation_never_raises_anything_but_parse_error() -> None:
    data = docs("with_attachment.msg")
    for size in range(0, len(data), 997):
        try:
            run("msg", data[:size])
        except ParseError:
            pass


def test_e4_6_msg_with_a_corrupt_header_does_not_crash() -> None:
    data = bytearray(docs("with_attachment.msg"))
    data[44:48] = struct.pack("<I", 0xFFFFFFFF)  # a corrupt sector count in the header
    try:
        run("msg", bytes(data))
    except ParseError as error:
        assert error.code == ErrorCode.CORRUPT
