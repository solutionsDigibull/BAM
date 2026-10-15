"""E0.17: the Outlook .msg fixture (`documents/with_attachment.msg`), for the E4.6 loader (#72).

The fixture is an OLE (CFB) file written by hand by `scripts/generate_test_fixtures.py`. These tests
read it back with `tests/msg_reader.py`, a separately written reader. In addition, when the fixture
was made it was opened with the real `olefile` (strictest defect level) and `extract-msg`, which
returned the same sender, subject, recipient, body and attachment bytes.
"""

import hashlib
import importlib.util
import re
import struct
from pathlib import Path

import pytest
from fixtures import fixture_path
from msg_reader import NotAMsgFileError, read_msg

REPO = Path(__file__).resolve().parents[3]
MSG = fixture_path("documents", "with_attachment.msg")
PDF = fixture_path("documents", "scanned_full.pdf")


def test_e0_17_t04_sender_subject_and_attachment_name_match() -> None:
    message = read_msg(MSG)
    assert message.sender_email == "rfq-desk@example.com"
    assert message.sender_name == "RFQ Desk"
    assert message.subject == "RFQ ADX-2026-014 with scanned drawing (Outlook)"
    assert [a.filename for a in message.attachments] == ["rfq_scan.pdf"]  # exactly one


def test_e0_17_the_attachment_is_the_scanned_pdf_byte_for_byte() -> None:
    (attachment,) = read_msg(MSG).attachments
    assert attachment.data == PDF.read_bytes()
    assert attachment.mime == "application/pdf"


def test_e0_17_recipient_body_and_message_class() -> None:
    message = read_msg(MSG)
    assert message.recipients == [("Sourcing", "sourcing@example.com")]
    assert message.message_class == "IPM.Note"
    assert "please find the scanned RFQ attached" in message.body


def test_e0_17_it_is_an_ole_compound_file_in_the_layout_outlook_uses() -> None:
    data = MSG.read_bytes()
    assert data[:8] == bytes.fromhex("d0cf11e0a1b11ae1")
    assert (
        struct.unpack_from("<HH", data, 26) == (3, 0xFFFE)
        or struct.unpack_from("<H", data, 26)[0] == 3
    )
    assert len(data) % 512 == 0  # whole sectors
    streams = read_msg(MSG).streams
    assert "__properties_version1.0" in streams
    assert "__nameid_version1.0/__substg1.0_00020102" in streams
    assert "__attach_version1.0_#00000000/__substg1.0_37010102" in streams
    assert "__recip_version1.0_#00000000/__substg1.0_3003001F" in streams


def test_e0_17_the_message_header_counts_one_recipient_and_one_attachment() -> None:
    header = read_msg(MSG).streams["__properties_version1.0"][:32]
    next_recipient, next_attachment, recipients, attachments = struct.unpack_from(
        "<IIII", header, 8
    )
    assert (recipients, attachments) == (1, 1) and next_recipient >= 1 and next_attachment >= 1


def test_e0_17_only_example_com_addresses_appear_in_the_message() -> None:
    texts = " ".join(read_msg(MSG).texts())
    addresses = set(re.findall(r"[\w.+-]+@[\w.-]+", texts))
    assert {"rfq-desk@example.com", "sourcing@example.com"} <= addresses
    assert {a for a in addresses if not a.endswith("@example.com")} == set()  # the Message-ID too


def test_e0_17_the_fixture_is_exactly_what_the_generator_writes() -> None:
    """Regenerating gives the same bytes, so SHA256SUMS never changes by accident."""
    spec = importlib.util.spec_from_file_location(
        "generate_test_fixtures", REPO / "scripts" / "generate_test_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build_msg(PDF.read_bytes()) == MSG.read_bytes()
    assert module.build_msg(PDF.read_bytes()) == module.build_msg(PDF.read_bytes())


def test_e0_17_the_checksum_is_listed() -> None:
    sums = (fixture_path("SHA256SUMS")).read_text(encoding="utf-8")
    digest = hashlib.sha256(MSG.read_bytes()).hexdigest()
    assert f"{digest}  documents/with_attachment.msg" in sums


@pytest.mark.parametrize(
    "broken",
    [b"", b"not an ole file at all", MSG.read_bytes()[:8]],
)
def test_e0_17_the_reader_refuses_what_is_not_a_compound_file(broken: bytes) -> None:
    with pytest.raises((NotAMsgFileError, struct.error, IndexError)):
        read_msg(broken)


def test_e0_17_the_writer_handles_small_and_large_streams() -> None:
    """Streams under 4096 bytes use the mini stream, the rest sectors; both must read back."""
    spec = importlib.util.spec_from_file_location(
        "generate_test_fixtures", REPO / "scripts" / "generate_test_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for size in (1, 63, 64, 65, 4095, 4096, 4097, 512 * 3, 200_000):
        data = bytes(range(256)) * (size // 256 + 1)
        data = data[:size]
        blob = module.build_cfb(
            module.CfbNode("Root Entry", children=[module.CfbNode("payload", data)])
        )
        assert read_msg(blob).streams["payload"] == data, size
