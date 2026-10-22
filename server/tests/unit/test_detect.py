"""E4.4 type detection: the pure rules of `sbam_workers.ingest.detect`, run on the golden fixtures.

The activity around it (storage, state machine, notification) is in test_type_detection.py.
"""

import gzip
import io
import zipfile
from pathlib import Path

import pytest
from fixtures import fixture_path
from sbam_api.filetypes.config import allowlist
from sbam_contracts.api import ErrorCode
from sbam_workers.ingest.detect import (
    HEAD_BYTES,
    Detected,
    Rejected,
    detect,
    enabled_rows,
    is_executable,
    is_text,
)

DOCS = "documents"


def read(name: str) -> bytes:
    return fixture_path(DOCS, name).read_bytes()


def run(name: str, data: bytes) -> Detected | Rejected:
    """What the activity does: the head, and the entry list for a ZIP container."""

    def names() -> set[str]:
        return set(zipfile.ZipFile(io.BytesIO(data)).namelist())

    return detect(name, data[:HEAD_BYTES], names)


def rejected(result: Detected | Rejected) -> ErrorCode:
    assert isinstance(result, Rejected), result
    return result.code


def detected(result: Detected | Rejected) -> Detected:
    assert isinstance(result, Detected), result
    return result


# --- every MVP type is detected from its fixture, and routed -------------------------------------

MVP = [
    ("sample.cpp", "cpp", "source-code"),
    ("with_attachment.eml", "eml", "email"),
    ("sample.go", "go", "source-code"),
    ("sample.js", "js", "source-code"),
    ("sample.json", "json", "structured"),
    ("sample.md", "md", "text"),
    ("with_attachment.msg", "msg", "email"),
    ("sample.py", "py", "source-code"),
    ("sample.sql", "sql", "source-code"),
    ("sample.txt", "txt", "text"),
    ("sample.xml", "xml", "structured"),
    ("sample.yaml", "yaml", "structured"),
    ("sample.csv", "csv", "tabular"),
    ("sample.docx", "docx", "docx"),
    ("text.pdf", "pdf", "pdf-text"),
    ("sample.tar.gz", "tar.gz", "archive"),
    ("multi_sheet.xlsx", "xlsx", "tabular"),
    ("sample.zip", "zip", "archive"),
    ("photo.jpg", "jpg", "ocr"),
    ("scan.png", "png", "ocr"),
    ("scan.tiff", "tiff", "ocr"),
]


@pytest.mark.parametrize(("name", "kind", "loader"), MVP)
def test_e4_4_every_mvp_fixture_is_detected_and_routed_to_its_loader(
    name: str, kind: str, loader: str
) -> None:
    found = detected(run(name, read(name)))
    assert (found.detected_type, found.loader) == (kind, loader)
    assert found.row_label.startswith("Filetype #")


def test_e4_4_a_container_that_carries_a_pdf_attachment_is_not_a_pdf() -> None:
    msg = read("with_attachment.msg")
    assert b"%PDF-" in msg[:1024] or b"%PDF-" in msg  # the attachment is in the file
    assert detected(run("rfq.msg", msg)).detected_type == "msg"
    assert rejected(run("rfq.pdf", msg)) is ErrorCode.TYPE_MISMATCH


def test_e4_4_t01_a_text_pdf_maps_to_the_text_pdf_row() -> None:
    found = detected(run("text.pdf", read("text.pdf")))
    assert found.detected_type == "pdf" and found.row_label == "Filetype #21"
    assert enabled_rows()["pdf"].frozen.file_format.startswith(".pdf")


def test_e4_4_t07_a_tar_gz_is_detected_and_routed_to_the_archive_loader() -> None:
    found = detected(run("archive.tar.gz", read("sample.tar.gz")))
    assert (found.detected_type, found.loader, found.row_label) == (
        "tar.gz",
        "archive",
        "Filetype #22",
    )


def test_e4_4_the_yml_extension_is_the_yaml_row() -> None:
    assert detected(run("config.yml", read("sample.yaml"))).detected_type == "yml"


# --- the content wins over the extension ---------------------------------------------------------


def test_e4_4_t02_a_windows_executable_named_pdf_is_a_type_mismatch() -> None:
    pe = (
        b"MZ"
        + b"\x90" * 58
        + (0x80).to_bytes(4, "little")
        + b"\x00" * 64
        + b"PE\x00\x00"
        + b"\x00" * 200
    )
    assert rejected(run("report.pdf", pe)) is ErrorCode.TYPE_MISMATCH


@pytest.mark.parametrize(
    "header",
    [
        b"\x7fELF\x02\x01\x01" + b"\x00" * 60,
        b"\xcf\xfa\xed\xfe" + b"\x00" * 60,
        b"\xca\xfe\xba\xbe" + b"\x00" * 60,
    ],
    ids=["elf", "mach-o", "universal-binary"],
)
@pytest.mark.parametrize("name", ["notes.txt", "tool.py", "doc.pdf", "data.zip"])
def test_e4_4_executables_are_always_rejected_whatever_their_name(name: str, header: bytes) -> None:
    assert rejected(run(name, header)) is ErrorCode.TYPE_MISMATCH


def test_e4_4_a_text_file_that_starts_with_mz_is_not_an_executable() -> None:
    assert not is_executable(b"MZ-42 purchase order\nline two\n")
    assert detected(run("po.txt", b"MZ-42 purchase order\nline two\n")).detected_type == "txt"


def test_e4_4_the_fixture_png_saved_as_pdf_is_a_type_mismatch() -> None:
    assert (
        rejected(run("fake_pdf_is_png.pdf", read("fake_pdf_is_png.pdf"))) is ErrorCode.TYPE_MISMATCH
    )


def test_e4_4_t03_a_docx_renamed_xlsx_is_a_type_mismatch_and_so_is_the_reverse() -> None:
    assert rejected(run("budget.xlsx", read("sample.docx"))) is ErrorCode.TYPE_MISMATCH
    assert rejected(run("letter.docx", read("multi_sheet.xlsx"))) is ErrorCode.TYPE_MISMATCH


def test_e4_4_a_zip_that_is_not_ooxml_is_not_a_docx() -> None:
    assert rejected(run("letter.docx", read("sample.zip"))) is ErrorCode.TYPE_MISMATCH


def test_e4_4_a_truncated_zip_is_a_type_mismatch_not_a_crash() -> None:
    assert rejected(run("letter.docx", read("sample.docx")[:300])) is ErrorCode.TYPE_MISMATCH


def test_e4_4_a_gzip_that_is_not_a_tar_is_a_type_mismatch() -> None:
    plain = gzip.compress(b"just some text, not a tar archive\n" * 40)
    assert rejected(run("backup.tar.gz", plain)) is ErrorCode.TYPE_MISMATCH


def test_e4_4_a_pdf_png_or_archive_named_like_text_is_a_type_mismatch() -> None:
    assert rejected(run("notes.txt", read("text.pdf"))) is ErrorCode.TYPE_MISMATCH
    assert rejected(run("notes.md", read("scan.png"))) is ErrorCode.TYPE_MISMATCH
    assert rejected(run("data.csv", read("sample.zip"))) is ErrorCode.TYPE_MISMATCH


def test_e4_4_text_named_pdf_is_a_type_mismatch() -> None:
    assert (
        rejected(run("report.pdf", b"This is plain text, not a PDF.\n")) is ErrorCode.TYPE_MISMATCH
    )


def test_e4_4_an_image_named_with_the_wrong_image_extension_is_a_type_mismatch() -> None:
    assert rejected(run("photo.png", read("photo.jpg"))) is ErrorCode.TYPE_MISMATCH


# --- text formats ---------------------------------------------------------------------------------


def test_e4_4_t04_a_markdown_file_with_invalid_utf8_is_invalid_encoding() -> None:
    assert (
        rejected(run("notes.md", b"# Title\n\xff\xfe broken \xc3\x28 bytes\n"))
        is ErrorCode.INVALID_ENCODING
    )


def test_e4_4_a_text_file_with_a_nul_byte_is_invalid_encoding() -> None:
    assert rejected(run("data.csv", b"a,b\n1,2\x00\n")) is ErrorCode.INVALID_ENCODING


def test_e4_4_utf16_text_is_invalid_encoding() -> None:
    assert rejected(run("notes.txt", "hello".encode("utf-16"))) is ErrorCode.INVALID_ENCODING


def test_e4_4_a_utf8_bom_is_allowed() -> None:
    assert (
        detected(run("notes.md", b"\xef\xbb\xbf# Title\nAccents: caf\xc3\xa9\n")).detected_type
        == "md"
    )


def test_e4_4_a_multibyte_character_cut_at_the_end_of_the_head_is_not_invalid() -> None:
    body = ("x" * (HEAD_BYTES - 1) + "é").encode()  # the two bytes of é straddle the boundary
    assert detected(run("big.txt", body)).detected_type == "txt"
    assert is_text(body[:HEAD_BYTES])


def test_e4_4_the_extension_decides_among_text_types() -> None:
    csv = read("sample.csv")
    assert detected(run("table.csv", csv)).loader == "tabular"
    assert detected(run("table.txt", csv)).loader == "text"  # same bytes, the other text type


# --- unsupported types ----------------------------------------------------------------------------


def test_e4_4_t08_a_future_type_with_a_valid_header_is_unsupported() -> None:
    mp3 = b"ID3\x03\x00\x00\x00\x00\x00\x21" + b"\x00" * 200
    assert rejected(run("song.mp3", mp3)) is ErrorCode.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    "name", ["drawing.dwg", "board.gbr", "page.html", "data.parquet", "noext", "x.exe"]
)
def test_e4_4_an_extension_without_an_enabled_filetype_row_is_unsupported(name: str) -> None:
    assert rejected(run(name, b"plain text content\n")) is ErrorCode.UNSUPPORTED_TYPE


def test_e4_4_media_named_like_text_is_a_type_mismatch() -> None:
    assert (
        rejected(run("notes.txt", b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40))
        is ErrorCode.TYPE_MISMATCH
    )


# --- the allowlist is the Filetype configuration -------------------------------------------------


def test_e4_4_every_allowlisted_extension_has_a_fixture_or_is_covered_by_a_sibling() -> None:
    covered = {kind for _, kind, _ in MVP} | {"yml", "jpeg"}  # yml shares row #12, jpeg is #30
    assert allowlist() <= covered


def test_e4_4_a_jpeg_extension_is_the_jpeg_row() -> None:
    assert detected(run("scan.jpeg", read("photo.jpg"))).row_label == "Filetype #30"


def test_e4_4_detection_never_reads_more_than_the_head_for_a_text_file() -> None:
    called: list[int] = []

    def names() -> set[str]:
        called.append(1)
        return set()

    detect("a.txt", b"hello\n", names)
    assert called == []


def test_e4_4_fixtures_are_where_the_tests_expect_them() -> None:
    assert Path(fixture_path(DOCS, "text.pdf")).stat().st_size > 0
