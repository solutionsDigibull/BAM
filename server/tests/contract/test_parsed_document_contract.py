"""E4.5 contract: `doc.json`, the parse report and the loader limits (loaders: tests/unit)."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts import documents
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import (
    Block,
    BlockType,
    DocumentOut,
    ParsedDocument,
    ParseOutcome,
    ParseReport,
    Table,
)

VERSION = uuid.uuid4()


def block(**fields: Any) -> Block:
    return Block.model_validate({"type": "paragraph", "text": "hello", **fields})


@pytest.mark.parametrize(
    "where",
    [{"page": 1}, {"sheet": "Sheet1"}, {"line": 7}, {"sheet": "Sheet1", "cell": "B4"}],
    ids=["page", "sheet", "line", "sheet-and-cell"],
)
def test_e4_5_a_block_says_where_it_came_from(where: dict[str, Any]) -> None:
    assert block(**where).model_dump(exclude_none=True)["text"] == "hello"


def test_e4_5_a_block_with_no_provenance_is_refused() -> None:
    with pytest.raises(ValidationError, match="page, sheet, cell or line"):
        block()


@pytest.mark.parametrize("cell", ["b4", "4B", "B0", "B", "AAAA1", "B4:C5", "Sheet1!B4"])
def test_e4_5_a_cell_is_an_a1_reference(cell: str) -> None:
    with pytest.raises(ValidationError):
        block(sheet="Sheet1", cell=cell)


def test_e4_5_a_cell_needs_its_sheet_or_page() -> None:
    with pytest.raises(ValidationError, match="its sheet"):
        block(cell="B4", line=1)


def test_e4_5_a_table_block_names_its_table() -> None:
    with pytest.raises(ValidationError, match="names its table"):
        Block(type=BlockType.TABLE, text="", page=1)
    assert Block(type=BlockType.TABLE, text="", page=1, table=0).table == 0


@pytest.mark.parametrize("level", [0, 7, -1])
def test_e4_5_a_heading_level_is_one_to_six(level: int) -> None:
    with pytest.raises(ValidationError):
        block(type="heading", line=1, level=level)


@pytest.mark.parametrize("page", [0, -3])
def test_e4_5_pages_and_lines_start_at_one(page: int) -> None:
    with pytest.raises(ValidationError):
        block(page=page)
    with pytest.raises(ValidationError):
        block(line=page)


def test_e4_5_unknown_fields_are_refused_everywhere() -> None:
    with pytest.raises(ValidationError):
        block(page=1, bbox=[0, 0, 1, 1])
    with pytest.raises(ValidationError):
        Table.model_validate({"header": [], "rows": [], "colour": "red"})
    with pytest.raises(ValidationError):
        ParseReport.model_validate({"pages": 1, "secret": "x"})
    with pytest.raises(ValidationError):
        ParsedDocument.model_validate({"document_version_id": str(VERSION), "loader": "x", "y": 1})


def test_e4_5_a_ten_page_pdf_document_round_trips() -> None:
    doc = ParsedDocument(
        document_version_id=VERSION,
        loader="pdf-text",
        blocks=[block(page=n, text=f"page {n}") for n in range(1, 11)],
    )
    again = ParsedDocument.model_validate_json(doc.model_dump_json())
    assert [b.page for b in again.blocks] == list(range(1, 11)) and again.schema_version == 1


def test_e4_5_a_spreadsheet_keeps_sheet_and_cell_apart() -> None:
    doc = ParsedDocument(
        document_version_id=VERSION,
        loader="tabular",
        blocks=[Block(type=BlockType.CELL, text="42", sheet="Sheet1", cell="B4")],
        tables=[Table(header=["a"], rows=[["1"]], sheet="Sheet1", range="A1:A2")],
    )
    dumped = doc.model_dump(mode="json", exclude_none=True)
    assert dumped["blocks"][0] == {
        "type": "cell",
        "text": "42",
        "sheet": "Sheet1",
        "cell": "B4",
        "unreadable": False,
    }
    assert dumped["tables"][0]["range"] == "A1:A2"


def test_e4_5_the_schema_version_is_pinned_to_one() -> None:
    with pytest.raises(ValidationError):
        ParsedDocument(document_version_id=VERSION, loader="x", schema_version=2)


def test_e4_5_the_report_defaults_to_nothing_found() -> None:
    assert ParseReport().model_dump() == {
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
    with pytest.raises(ValidationError):
        ParseReport(pages=-1)


def test_e4_5_the_failure_codes_are_error_codes() -> None:
    for code in ("CORRUPT", "ENCRYPTED", "EMPTY", "TIMEOUT", "PARSE_ERROR"):
        assert ErrorCode(code).value == code


def test_e4_5_a_failed_outcome_carries_a_code_and_a_parsed_one_a_report() -> None:
    failed = ParseOutcome(document_version_id=VERSION, parsed=False, code=ErrorCode.ENCRYPTED)
    assert failed.report is None and failed.code is ErrorCode.ENCRYPTED
    done = ParseOutcome(document_version_id=VERSION, parsed=True, report=ParseReport(pages=3))
    assert done.code is None and done.report is not None and done.report.pages == 3


def test_e4_5_a_document_shows_why_its_parse_failed() -> None:
    base: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "title": "x.pdf",
        "state": "PARSE_FAILED",
        "size": 1,
        "mime": "application/pdf",
        "sha256": "0" * 64,
        "created_at": "2026-10-22T10:00:00Z",
    }
    plain = DocumentOut.model_validate(base)
    assert plain.parse_code is None and plain.parse_report is None
    failed = DocumentOut.model_validate(
        {**base, "parse_code": "ENCRYPTED", "parse_report": {"pages": 2}}
    )
    assert failed.parse_code is ErrorCode.ENCRYPTED and failed.parse_report == ParseReport(pages=2)


def test_e4_5_the_loader_limits_are_the_ones_in_the_issue() -> None:
    assert documents.MAX_PDF_PAGES == 2000
    assert documents.MAX_XLSX_CELLS == 1_000_000
    assert documents.PARSE_TIMEOUT_SECONDS == 600
    assert documents.PARSE_HEARTBEAT_SECONDS == 60
    assert documents.PARSE_MEMORY_LIMIT_BYTES == 1024**3
    assert documents.PARSE_HEARTBEAT_SECONDS < documents.PARSE_TIMEOUT_SECONDS
