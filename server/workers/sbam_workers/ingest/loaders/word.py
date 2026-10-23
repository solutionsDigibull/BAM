"""DOCX (E4.5): headings, list items, paragraphs and tables, in document order.

Read with python-docx: no macro, no field and no link is run or followed, embedded objects are
ignored. A Word file has no pages and no lines the library can give, so `line` is the 1-based
ordinal of the block in the document (a table is one block, then one block per row). Heading
styles (`Title`, `Heading 1` to `Heading 9`) become headings, level capped at 6; a paragraph in a
list style or with numbering becomes a list item. A document with no text and no table is EMPTY.
"""

import io
import logging

from docx import Document
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, BlockType, ParseReport, Table

from sbam_workers.ingest.loaders.base import (
    BEAT_EVERY,
    Heartbeat,
    Parsed,
    ParseError,
    check_zip,
    gfm_table,
)

log = logging.getLogger("sbam_workers.ingest.loaders.word")


def load_docx(data: bytes, beat: Heartbeat) -> Parsed:
    check_zip(data)
    try:
        document = Document(io.BytesIO(data))
        children = list(document.element.body.iterchildren())
    except Exception as exc:  # a zip without the Word parts, bad XML, ...
        log.info("unreadable DOCX", extra={"exc_type": type(exc).__name__})
        raise ParseError(ErrorCode.CORRUPT, "the file is not a readable Word document") from None

    blocks: list[Block] = []
    tables: list[Table] = []
    markdown: list[str] = []
    for index, child in enumerate(children, start=1):
        if index % BEAT_EVERY == 0:
            beat()
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            _paragraph(Paragraph(child, document), blocks, markdown)
        elif tag == "tbl":
            _table(DocxTable(child, document), blocks, tables, markdown)
    if not blocks:
        raise ParseError(ErrorCode.EMPTY, "the document has no text")
    return Parsed(
        blocks=blocks,
        tables=tables,
        markdown="\n".join(markdown),
        report=ParseReport(tables=len(tables)),
    )


def _paragraph(paragraph: Paragraph, blocks: list[Block], markdown: list[str]) -> None:
    text = paragraph.text.strip()
    if not text:
        return
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    ordinal = len(blocks) + 1
    level = _heading_level(style)
    if level is not None:
        blocks.append(Block(type=BlockType.HEADING, text=text, level=level, line=ordinal))
        _end_list(markdown)
        markdown.append(f"{'#' * level} {text}\n")
    elif style.startswith("List") or _is_numbered(paragraph):
        blocks.append(Block(type=BlockType.LIST_ITEM, text=text, line=ordinal))
        markdown.append(f"- {text}")
    else:
        blocks.append(Block(type=BlockType.PARAGRAPH, text=text, line=ordinal))
        _end_list(markdown)
        markdown.append(f"{text}\n")


def _heading_level(style: str) -> int | None:
    if style == "Title":
        return 1
    if style.startswith("Heading "):
        number = style.removeprefix("Heading ").strip()
        if number.isdigit():
            return max(1, min(int(number), 6))
    return None


def _is_numbered(paragraph: Paragraph) -> bool:
    properties = paragraph._p.pPr  # the paragraph's own properties, if it has any
    return properties is not None and properties.numPr is not None


def _table(table: DocxTable, blocks: list[Block], tables: list[Table], markdown: list[str]) -> None:
    rows: list[list[str]] = [
        [" ".join(cell.text.split()) for cell in row.cells] for row in table.rows
    ]
    rows = [r for r in rows if any(r)]
    if not rows:
        return
    header, body = rows[0], rows[1:]
    blocks.append(Block(type=BlockType.TABLE, text="", line=len(blocks) + 1, table=len(tables)))
    for row in rows:
        blocks.append(Block(type=BlockType.ROW, text=" | ".join(row), line=len(blocks) + 1))
    tables.append(Table(header=header, rows=body))
    _end_list(markdown)
    markdown.append("".join(gfm_table(header, body)))


def _end_list(markdown: list[str]) -> None:
    """A blank line after the last list item, or the next paragraph would continue the item."""
    if markdown and markdown[-1].startswith("- "):
        markdown.append("")
