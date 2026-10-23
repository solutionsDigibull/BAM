"""E4.8 contract: OCR blocks, the report fields, the model's answer and the OCR limits."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts import documents
from sbam_contracts.documents import Block, OcrPage, ParsedDocument, ParseReport


def block(**fields: Any) -> Block:
    return Block.model_validate({"type": "paragraph", "text": "scanned text", "page": 1, **fields})


# --- blocks ---------------------------------------------------------------------------------------


def test_e4_8_a_block_read_by_the_model_says_so() -> None:
    assert block(ocr="llm").ocr == "llm"
    assert block().ocr is None and block().unreadable is False  # text blocks are unchanged


@pytest.mark.parametrize("source", ["tesseract", "LLM", "", "local"])
def test_e4_8_the_only_ocr_source_is_the_model(source: str) -> None:
    with pytest.raises(ValidationError):
        block(ocr=source)


def test_e4_8_an_unreadable_page_is_an_empty_ocr_block_of_that_page() -> None:
    gone = block(text="", ocr="llm", unreadable=True, page=3)
    assert (gone.page, gone.text, gone.unreadable) == (3, "", True)


@pytest.mark.parametrize(
    "fields",
    [
        {"text": "x", "ocr": "llm", "unreadable": True},  # an unreadable block has no text
        {"text": "", "unreadable": True},  # and it is an OCR block
        {"text": "", "ocr": "llm", "unreadable": True, "page": None, "line": 1},  # of a page
    ],
    ids=["has-text", "not-ocr", "no-page"],
)
def test_e4_8_an_unreadable_block_must_be_empty_ocr_for_a_page(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="unreadable block"):
        block(**fields)


def test_e4_8_a_document_with_ocr_blocks_round_trips_and_old_documents_still_validate() -> None:
    doc = ParsedDocument(
        document_version_id=uuid.uuid4(),
        loader="Filetype #21",
        blocks=[block(page=1, text="typed"), block(page=2, ocr="llm")],
    )
    again = ParsedDocument.model_validate_json(doc.model_dump_json(exclude_none=True))
    assert [b.ocr for b in again.blocks] == [None, "llm"]
    old = (
        '{"document_version_id": "%s", "loader": "x", '
        '"blocks": [{"type": "paragraph", "text": "a", "page": 1}]}'
    )
    assert ParsedDocument.model_validate_json(old % uuid.uuid4()).blocks[0].unreadable is False


# --- the report -----------------------------------------------------------------------------------


def test_e4_8_the_report_lists_the_pages_read_and_the_pages_not_readable() -> None:
    report = ParseReport(ocr_needed_pages=[1, 2, 3], ocr_pages=[1, 3], unreadable_pages=[2])
    assert report.model_dump()["ocr_pages"] == [1, 3]
    assert ParseReport().ocr_pages == [] and ParseReport().unreadable_pages == []


# --- the model's answer ---------------------------------------------------------------------------


def test_e4_8_an_answer_is_a_page_markdown_and_a_flag() -> None:
    answer = OcrPage.model_validate({"page": 2, "markdown": "# Invoice\n\nTotal 12"})
    assert (answer.page, answer.unreadable) == (2, False)
    assert OcrPage(page=3, markdown="", unreadable=True).unreadable is True


@pytest.mark.parametrize(
    "bad",
    [
        {"page": 1, "markdown": "x", "status": "APPROVED"},  # a field the model invented
        {"page": 1, "markdown": "x", "is_locked": True},
        {"page": 0, "markdown": "x"},
        {"markdown": "x"},
        {"page": 1},
        {"page": "one", "markdown": "x"},
        {"page": 1, "markdown": "x" * 200_001},
    ],
    ids=["status", "is_locked", "page-zero", "no-page", "no-markdown", "page-text", "too-long"],
)
def test_e4_8_nothing_but_the_three_fields_is_accepted(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OcrPage.model_validate(bad)


def test_e4_8_plain_text_is_not_an_answer() -> None:
    with pytest.raises(ValidationError):
        OcrPage.model_validate_json("Here is the text of the page: ...")


# --- the limits -----------------------------------------------------------------------------------


def test_e4_8_the_ocr_limits_are_the_ones_in_the_issue() -> None:
    assert documents.MIN_TEXT_CHARS == 20
    assert documents.MAX_OCR_PAGES == 200
    assert documents.OCR_DPI == 200
    assert documents.OCR_MAX_SIDE_PX == 3000
    assert documents.OCR_MAX_IMAGE_BYTES == 5 * 1024 * 1024
    assert documents.OCR_PAGE_ATTEMPTS == 3
    assert documents.OCR_HEARTBEAT_SECONDS < documents.OCR_TIMEOUT_SECONDS
    assert documents.OCR_TIMEOUT_SECONDS > documents.PARSE_TIMEOUT_SECONDS  # up to 200 model calls
