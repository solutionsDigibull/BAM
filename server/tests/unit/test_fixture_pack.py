"""E0.8 unit tests: the golden fixture pack (checksums, README, EICAR, sizes, PII scan)."""

import base64
import email
import email.policy
import hashlib
import io
import json
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path

import openpyxl
import pytest
from fixtures import FIXTURES_DIR, MAX_FILE_BYTES, blob, copy_to_tmp, eicar_bytes, fixture_path
from fixtures.check_fixture_readme import fixture_files, undocumented
from msg_reader import read_msg

REPO = Path(__file__).resolve().parents[3]
DOCS = FIXTURES_DIR / "documents"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)|\d{2,4})[ .-]\d{3,4}[ .-]\d{3,4}(?![\w.])"
)
SECRETS = [
    re.compile(r"\bsk-[A-Za-z0-9-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}"),
]


# --- E0.8-T02: checksums (automated twin of the manual `sha256sum -c`) ---------------------------


def read_sums() -> dict[str, str]:
    lines = (FIXTURES_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    return {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in lines}


def test_e0_8_t02_every_checksum_matches() -> None:
    mismatches = [
        name
        for name, digest in read_sums().items()
        if hashlib.sha256((FIXTURES_DIR / name).read_bytes()).hexdigest() != digest
    ]
    assert mismatches == []


def test_e0_8_t02_checksums_cover_every_fixture_file_and_nothing_else() -> None:
    assert set(read_sums()) == set(fixture_files())  # the README and helper code are not fixtures


# --- E0.8-T03: README completeness ---------------------------------------------------------------


def test_e0_8_t03_every_file_is_documented() -> None:
    assert undocumented() == []
    assert len(fixture_files()) == 38


def test_e0_8_t03_readme_lists_no_file_that_does_not_exist() -> None:
    readme = (FIXTURES_DIR / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"`((?:documents|isa14|masters|sow|malware)/[^`]+)`", readme)
    assert listed
    assert [name for name in listed if not (FIXTURES_DIR / name).is_file()] == []


def test_e0_8_t03_readme_flags_the_pack_as_synthetic() -> None:
    readme = (FIXTURES_DIR / "README.md").read_text(encoding="utf-8")
    assert "synthetic" in readme.lower()


# --- E0.8-T04: EICAR ------------------------------------------------------------------------------


def test_e0_8_t04_eicar_is_the_68_byte_test_string() -> None:
    data = eicar_bytes()
    assert len(data) == 68
    assert data.startswith(b"X5O!P%@AP")
    assert data.endswith(b"$H+H*")
    assert (
        b"STANDARD-ANTIVIRUS" + b"-TEST-FILE" in data
    )  # needle built in pieces: see the tree scan below


EICAR_SHA256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"  # public value


def test_e0_17_t03_the_joined_halves_are_exactly_the_eicar_string() -> None:
    assert hashlib.sha256(eicar_bytes()).hexdigest() == EICAR_SHA256
    assert len(eicar_bytes()) == 68


def test_e0_17_t03_neither_stored_half_is_the_eicar_string_in_any_form() -> None:
    whole = eicar_bytes()
    parts = [fixture_path("malware", name) for name in ("eicar.part1.b64", "eicar.part2.b64")]
    decoded = [base64.b64decode(part.read_text("ascii").strip()) for part in parts]
    assert b"".join(decoded) == whole
    for part, piece in zip(parts, decoded, strict=True):
        assert piece != whole and piece in whole and 0 < len(piece) < len(whole)
        assert base64.b64encode(whole) not in part.read_bytes()  # not the whole thing, encoded
        assert whole not in part.read_bytes()
    assert not (FIXTURES_DIR / "malware" / "eicar.b64").exists()  # the one-piece file is gone


def test_e0_8_t04_eicar_literal_is_not_in_the_repository_tree() -> None:
    needles = [
        eicar_bytes(),
        base64.b64encode(eicar_bytes()),  # the whole string in one base64 piece (#202)
        b"STANDARD-ANTIVIRUS" + b"-TEST-FILE",
    ]
    roots = [REPO / "server/tests", REPO / "scripts", REPO / "docs", REPO / "spikes"]
    hits = [
        str(path.relative_to(REPO))
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and ".venv" not in path.parts
        and "__pycache__" not in path.parts
        and path.name != ".env"
        and any(n in path.read_bytes() for n in needles)
    ]
    assert hits == []


# --- E0.8-T05: size boundaries -------------------------------------------------------------------


def test_e0_8_t05_blob_has_exact_sizes_at_the_15_mb_boundary() -> None:
    assert len(blob(15_728_640)) == 15_728_640
    assert len(blob(15_728_641)) == 15_728_641
    assert MAX_FILE_BYTES == 15_728_640


def test_e0_8_t05_blob_is_deterministic_and_handles_edges() -> None:
    assert blob(0) == b""
    assert blob(1) == b"S"
    assert blob(100) == blob(100)
    with pytest.raises(ValueError, match="negative"):
        blob(-1)


def test_e0_8_t05_size_limits_file_matches_the_helper() -> None:
    limits = json.loads(fixture_path("malware", "size_limits.json").read_text("utf-8"))
    assert limits["max_file_bytes"] == MAX_FILE_BYTES
    assert limits["over_limit_bytes"] == MAX_FILE_BYTES + 1
    assert limits["max_files_per_project"] == 20


def test_e0_8_t05_no_stored_fixture_is_large() -> None:
    assert max(p.stat().st_size for p in FIXTURES_DIR.rglob("*") if p.is_file()) < 400_000


# --- E0.8-T06: no secrets or PII -----------------------------------------------------------------


def searchable_text(path: Path) -> Iterator[str]:
    suffixes = "".join(path.suffixes).lower()
    data = path.read_bytes()
    if suffixes.endswith(".msg"):  # UTF-16 properties: decode the text ones, skip the attachment
        yield from read_msg(path).texts()
    elif suffixes.endswith((".docx", ".xlsx", ".zip")):
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                yield archive.read(member).decode("utf-8", errors="ignore")
    elif suffixes.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            for entry in archive.getmembers():
                extracted = archive.extractfile(entry)
                yield (extracted.read() if extracted else b"").decode("utf-8", errors="ignore")
    elif data.startswith(b"%PDF"):
        yield re.sub(rb"stream.*?endstream", b"", data, flags=re.DOTALL).decode("latin-1")
    elif suffixes.endswith((".png", ".jpg", ".tiff")) or data.startswith(b"\x89PNG"):
        return
    else:
        yield data.decode("utf-8", errors="ignore")


def test_e0_8_t06_only_example_com_addresses_appear() -> None:
    found = {
        match
        for path in FIXTURES_DIR.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS" and "__pycache__" not in path.parts
        for text in searchable_text(path)
        for match in EMAIL.findall(text)
    }
    assert found
    assert {a for a in found if not a.lower().endswith("@example.com")} == set()


def test_e0_8_t06_no_phone_numbers_or_secrets() -> None:
    problems = []
    for path in FIXTURES_DIR.rglob("*"):
        if not path.is_file() or path.name == "SHA256SUMS" or "__pycache__" in path.parts:
            continue
        for text in searchable_text(path):
            problems += [(path.name, m) for m in PHONE.findall(text)]
            problems += [(path.name, p.pattern) for p in SECRETS if p.search(text)]
    assert problems == []


def test_e0_8_t06_the_scan_actually_detects_what_it_looks_for() -> None:
    assert EMAIL.search("write to jane.doe@corp.test now")
    assert PHONE.search("call 415-555-0142 today")
    assert PHONE.search("call +1 415 555 0142 today")
    assert not PHONE.search("delivery by 2026-12-01 for 2,500 units at 0.012")


# --- Documents: the edge cases the issue names ---------------------------------------------------


def pdf_facts(name: str) -> tuple[int, int, bool]:
    data = (DOCS / name).read_bytes()
    return (
        len(re.findall(rb"/Type /Page[^s]", data)),
        len(re.findall(rb"/Subtype /Image", data)),
        b"Request for Quotation" in data,
    )


def test_e0_8_documents_pdf_text_scanned_and_mixed() -> None:
    assert pdf_facts("text.pdf") == (2, 0, True)
    assert pdf_facts("scanned_full.pdf") == (2, 2, False)
    assert pdf_facts("scanned_mixed.pdf") == (2, 1, True)  # one text page, one image-only page


def test_e0_8_documents_misleading_extension_has_png_magic_bytes() -> None:
    data = (DOCS / "fake_pdf_is_png.pdf").read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert not data.startswith(b"%PDF")


def test_e0_8_documents_eml_attachment_is_the_scanned_pdf() -> None:
    message = email.message_from_bytes(
        (DOCS / "with_attachment.eml").read_bytes(), policy=email.policy.default
    )
    attachments = list(message.iter_attachments())
    assert [a.get_filename() for a in attachments] == ["rfq_scan.pdf"]
    assert attachments[0].get_content() == (DOCS / "scanned_full.pdf").read_bytes()
    assert message["From"] == "rfq-desk@example.com"


def test_e0_8_documents_xlsx_has_several_sheets_and_merged_cells() -> None:
    book = openpyxl.load_workbook(DOCS / "multi_sheet.xlsx")
    assert book.sheetnames == ["BOM", "Suppliers", "Notes"]
    assert [str(r) for r in book["BOM"].merged_cells.ranges] == ["A1:E1"]
    assert [str(r) for r in book["Notes"].merged_cells.ranges] == ["A1:B2"]


def test_e0_8_documents_archives_hold_two_members() -> None:
    assert zipfile.ZipFile(DOCS / "sample.zip").namelist() == ["rfq_summary.md", "bom_extract.csv"]
    with tarfile.open(DOCS / "sample.tar.gz") as archive:
        assert archive.getnames() == ["rfq_summary.md", "bom_extract.csv"]


def test_e0_8_documents_images_have_the_right_magic_bytes() -> None:
    assert (DOCS / "scan.png").read_bytes().startswith(b"\x89PNG")
    assert (DOCS / "photo.jpg").read_bytes().startswith(b"\xff\xd8\xff")
    assert (DOCS / "scan.tiff").read_bytes()[:4] in (b"II*\x00", b"MM\x00*")


def test_e0_8_documents_structured_and_source_files_parse() -> None:
    import ast

    json.loads((DOCS / "sample.json").read_text("utf-8"))
    json.loads((DOCS / "fastmcp_spec.json").read_text("utf-8"))
    ET.parse(DOCS / "sample.xml")  # noqa: S314 - our own trusted fixture file
    ast.parse((DOCS / "sample.py").read_text("utf-8"))
    assert "openapi: 3.0.3" in (DOCS / "openapi.yaml").read_text("utf-8")


def test_e0_8_documents_docx_is_a_zip_with_a_document_part() -> None:
    with zipfile.ZipFile(DOCS / "sample.docx") as archive:
        assert "word/document.xml" in archive.namelist()


def test_e0_8_t07_every_mvp_loader_extension_has_a_fixture() -> None:
    wanted = {
        ".md",
        ".txt",
        ".eml",
        ".msg",
        ".json",
        ".xml",
        ".yaml",
        ".py",
        ".sql",
        ".js",
        ".go",
        ".cpp",
        ".csv",
        ".xlsx",
        ".docx",
        ".pdf",
        ".zip",
        ".gz",
        ".png",
        ".jpg",
        ".tiff",
    }
    present = {p.suffix.lower() for p in DOCS.iterdir()}
    assert wanted <= present


# --- Helpers -------------------------------------------------------------------------------------


def test_e0_8_fixtures_are_read_only_inputs_copied_before_mutation(tmp_path: Path) -> None:
    original = fixture_path("documents", "sample.csv")
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    copy = copy_to_tmp("documents", "sample.csv", tmp_path=tmp_path)
    copy.write_text("changed\n", encoding="utf-8")
    assert copy.parent == tmp_path
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before


def test_e0_8_fixture_path_rejects_unknown_names() -> None:
    with pytest.raises(FileNotFoundError):
        fixture_path("documents", "nope.pdf")
    assert io.BytesIO(fixture_path("sow", "sow_rfq_nav9.json").read_bytes()).read(1) == b"{"
