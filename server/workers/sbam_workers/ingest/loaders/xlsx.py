"""XLSX (E4.5): one table per sheet, one block per non-empty cell with its sheet and cell (`B4`).

Read with openpyxl in read-only mode: nothing is executed and no link is followed (`keep_links` is
off, so external workbook links are not even loaded). Formulas are never evaluated. A cell's value
is the cached result Excel saved with the file; a formula that has no cached result is kept as its
own text (`=WEBSERVICE("...")` stays a string), so nothing is lost and nothing is run. Two passes
over the file give that: one for the cached values, one for the formulas, read side by side.

Limits: at most 1,000,000 non-empty cells in the workbook, and a used area of at most 5x that
(a sheet that declares `A1:XFD1048576` for a handful of cells is refused, not walked). Hidden
sheets are read and noted in the report; a merged range keeps its value in the top-left cell, as
the file does. Dates are ISO 8601, booleans TRUE or FALSE, whole numbers have no `.0`.

Memory: the cells are held once as text, in a grid per sheet. The blocks and `doc.md` are
generators over those grids, so the activity can write them out piece by piece.
"""

import datetime
import io
import logging
from collections.abc import Iterator
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import MAX_XLSX_CELLS, Block, BlockType, ParseReport, Table

from sbam_workers.ingest.loaders.base import (
    BEAT_EVERY,
    Heartbeat,
    Parsed,
    ParseError,
    check_zip,
    gfm_table,
)

log = logging.getLogger("sbam_workers.ingest.loaders.xlsx")
MAX_GRID_CELLS = 5 * MAX_XLSX_CELLS
MAX_WARNINGS = 20


def load_xlsx(data: bytes, beat: Heartbeat) -> Parsed:
    check_zip(data)
    try:
        values = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
        formulas = load_workbook(
            io.BytesIO(data), read_only=True, data_only=False, keep_links=False
        )
    except Exception as exc:  # a zip without the workbook parts, bad XML, ...
        log.info("unreadable XLSX", extra={"exc_type": type(exc).__name__})
        raise ParseError(ErrorCode.CORRUPT, "the file is not a readable workbook") from None
    try:
        return _read(values, formulas, beat)
    except ParseError:
        raise
    except Exception as exc:
        log.info("unreadable XLSX sheet", extra={"exc_type": type(exc).__name__})
        raise ParseError(ErrorCode.CORRUPT, "the workbook could not be read") from None
    finally:
        values.close()
        formulas.close()


def _read(values: Any, formulas: Any, beat: Heartbeat) -> Parsed:
    names: list[str] = []
    grids: list[list[list[str]]] = []
    warnings: list[str] = []
    cells = 0
    for sheet_v, sheet_f in zip(values.worksheets, formulas.worksheets, strict=True):
        sheet_v.reset_dimensions()  # a missing or wrong `<dimension>` must not hide rows
        sheet_f.reset_dimensions()
        grid: list[list[str]] = []
        width = 0
        for number, (row_v, row_f) in enumerate(
            zip(
                sheet_v.iter_rows(values_only=True),
                sheet_f.iter_rows(values_only=True),
                strict=False,  # the two passes see the same rows
            ),
            start=1,
        ):
            if number % BEAT_EVERY == 0:
                beat()
            row = [_text(v, f) for v, f in zip(row_v, row_f, strict=False)]
            used = sum(1 for c in row if c)
            if used:
                cells += used
                width = max(width, max(i for i, c in enumerate(row, 1) if c))
            if cells > MAX_XLSX_CELLS:
                raise ParseError(ErrorCode.PARSE_ERROR, f"more than {MAX_XLSX_CELLS} cells")
            grid.append(row)
            if len(grid) * max(width, len(row)) > MAX_GRID_CELLS:
                raise ParseError(ErrorCode.PARSE_ERROR, "the used area of the sheet is too large")
        while grid and not any(grid[-1]):  # trailing empty rows
            grid.pop()
        if not grid:
            continue
        names.append(sheet_v.title)
        grids.append([row[:width] + [""] * (width - len(row)) for row in grid])
        if sheet_v.sheet_state != "visible" and len(warnings) < MAX_WARNINGS:
            warnings.append(f"sheet '{sheet_v.title}' is hidden")
    if not grids:
        raise ParseError(ErrorCode.EMPTY, "the workbook has no values")

    tables = [
        Table(
            header=grid[0],
            rows=grid[1:],
            sheet=name,
            range=f"A1:{get_column_letter(max(len(grid[0]), 1))}{len(grid)}",
        )
        for name, grid in zip(names, grids, strict=True)
    ]
    report = ParseReport(sheets=len(names), tables=len(tables), warnings=warnings)
    return Parsed(
        blocks=_blocks(tables),
        tables=tables,
        markdown=_markdown(tables),
        report=report,
    )


def _blocks(tables: list[Table]) -> Iterator[Block]:
    for index, table in enumerate(tables):
        sheet = table.sheet or ""
        yield Block(type=BlockType.TABLE, text="", sheet=sheet, table=index)
        for r, row in enumerate([table.header, *table.rows], start=1):
            for c, value in enumerate(row, start=1):
                if value:
                    cell = f"{get_column_letter(c)}{r}"
                    yield Block(type=BlockType.CELL, text=value, sheet=sheet, cell=cell)


def _markdown(tables: list[Table]) -> Iterator[str]:
    for table in tables:
        yield f"## {table.sheet}\n\n"
        yield from gfm_table(table.header, table.rows)
        yield "\n"


def _text(value: Any, formula: Any) -> str:
    """The cached value as text, or the formula's own text when it has no cached value."""
    if value is None:
        return formula if isinstance(formula, str) and formula.startswith("=") else ""
    return _show(value)


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    text = str(value)
    return "".join(c for c in text if c in "\n\t" or c.isprintable())
