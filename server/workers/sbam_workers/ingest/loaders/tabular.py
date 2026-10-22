"""CSV (E4.5): a table with its header and one row block per line, with the line it came from.

The delimiter is sniffed from the first 8 KB among `,` `;` tab and `|` (a semicolon file from a
European Excel works); a BOM is dropped. A value is always text, never evaluated: `=WEBSERVICE(...)`
stays a string. A quoted value may span lines, so a row's `line` is the line it starts on. A row
with more or fewer fields than the header is padded or kept as is and noted in the report.
"""

import csv
import io

from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import Block, BlockType, ParseReport, Table

from sbam_workers.ingest.loaders.base import BEAT_EVERY, Heartbeat, Parsed, ParseError, decode

DELIMITERS = ",;\t|"
SNIFF_CHARS = 8192
MAX_WARNINGS = 20  # a badly ragged file gets a count, not a page of warnings


def load_csv(data: bytes, beat: Heartbeat) -> Parsed:
    text = decode(data)
    if not text.strip():
        raise ParseError(ErrorCode.EMPTY, "the file has no rows")
    reader = csv.reader(io.StringIO(text, newline=""), _dialect(text))
    rows: list[tuple[int, list[str]]] = []  # (line the row starts on, cells)
    last = 0
    try:
        for cells in reader:
            if any(c.strip() for c in cells):  # blank lines are not rows
                rows.append((last + 1, cells))
            last = reader.line_num
            if len(rows) % BEAT_EVERY == 0:
                beat()
    except csv.Error as exc:
        raise ParseError(ErrorCode.CORRUPT, f"the file is not valid CSV: {exc}") from None
    if not rows:
        raise ParseError(ErrorCode.EMPTY, "the file has no rows")

    (header_line, header), body = rows[0], rows[1:]
    width = len(header)
    warnings: list[str] = []
    ragged = 0
    table_rows: list[list[str]] = []
    blocks = [Block(type=BlockType.TABLE, text="", line=header_line, table=0)]
    blocks.append(Block(type=BlockType.ROW, text=" | ".join(header), line=header_line))
    for line, cells in body:
        if len(cells) != width:
            ragged += 1
            if len(warnings) < MAX_WARNINGS:
                warnings.append(f"line {line} has {len(cells)} fields, the header has {width}")
        table_rows.append(cells + [""] * (width - len(cells)) if len(cells) < width else cells)
        blocks.append(Block(type=BlockType.ROW, text=" | ".join(cells), line=line))
    if ragged > len(warnings):
        warnings.append(f"{ragged - len(warnings)} more rows have a different number of fields")

    table = Table(header=header, rows=table_rows, start_line=header_line)
    report = ParseReport(tables=1, warnings=warnings)
    return Parsed(
        blocks=blocks, tables=[table], markdown=_markdown(header, table_rows), report=report
    )


def _dialect(text: str) -> type[csv.Dialect]:
    try:
        return csv.Sniffer().sniff(text[:SNIFF_CHARS], delimiters=DELIMITERS)
    except csv.Error:
        return csv.excel


def _cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", r"\|").replace("\n", " ")


def _markdown(header: list[str], rows: list[list[str]]) -> str:
    def line(cells: list[str]) -> str:
        return "| " + " | ".join(_cell(c) for c in cells) + " |"

    out = [line(header), "| " + " | ".join("---" for _ in header) + " |"]
    out.extend(line(r) for r in rows)
    return "\n".join(out) + "\n"
