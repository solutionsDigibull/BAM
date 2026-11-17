#!/usr/bin/env python3
"""Build the Masters 0.3 workbook layout used by the masters export and template (#781, ADR 0029).

    cd server && uv run python ../scripts/build_masters_template.py

Writes `server/api/sbam_api/masters/template.xlsx`:
  * styling, column widths and the 43 frozen Filetype rows from the MSC reference workbook
    (`docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx`);
  * the five editable tabs (Organization, Rules, Agents, Skills, Tooling) with the approved Admin
    Masters UI columns from `sbam_contracts.masters_v03.COLUMNS`: row 1 Metadata numbers, row 2
    the headers (column A is the id column), row 3 the descriptions; data starts on row 4;
  * a README that lists the tabs and their columns.

Re-run it only when the structure changes; commit the result.
"""

from copy import copy
from pathlib import Path

import openpyxl
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_v03 import COLUMNS, STRUCTURE_VERSION
from sbam_contracts.masters_workbook import TABS, TITLE_PREFIX

ROOT = Path(__file__).resolve().parents[1]
MSC = ROOT / "docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx"
OUT = ROOT / "server/api/sbam_api/masters/template.xlsx"
EDITABLE = (
    MasterType.ORGANIZATION,
    MasterType.RULES,
    MasterType.AGENTS,
    MasterType.SKILLS,
    MasterType.TOOLING,
)


def _master_tab(book: openpyxl.Workbook, master_type: MasterType) -> None:
    tab = TABS[master_type]
    style_from = book["Skills"]  # the MSC header styling every tab shares
    if tab in book.sheetnames and tab != "Skills":
        del book[tab]
    sheet = style_from if tab == "Skills" else book.copy_worksheet(style_from)
    sheet.title = tab
    sheet.freeze_panes = style_from.freeze_panes
    styles = [[copy(style_from.cell(r, c)._style) for c in (1, 2)] for r in (1, 2, 3)]
    sheet.delete_rows(1, sheet.max_row)
    cols = COLUMNS[master_type]
    rows = [
        ["Metadata", *range(1, len(cols))],
        [cols[0][1], *(header for _, header, _ in cols[1:])],
        ["Description", *(description for _, _, description in cols[1:])],
    ]
    for r, values in enumerate(rows, start=1):
        for c, value in enumerate(values, start=1):
            cell = sheet.cell(r, c, value)
            cell._style = copy(styles[r - 1][0 if c == 1 else 1])


def _readme(book: openpyxl.Workbook) -> None:
    sheet = book["README"]
    sheet.delete_rows(1, sheet.max_row)
    rows: list[list[str]] = [
        [f"{TITLE_PREFIX} - {STRUCTURE_VERSION}"],
        ["The manager-approved Admin Masters UI columns (ADR 0029). Column A is the id column."],
        [],
        ["Tab", "Column", "Description"],
    ]
    for master_type in EDITABLE:
        for _, header, description in COLUMNS[master_type]:
            rows.append([TABS[master_type], header, description])
    rows.append(["Filetype", "-", "43 frozen rows; changed only by a super-admin (O-23)."])
    for values in rows:
        sheet.append(values)


def main() -> None:
    book = openpyxl.load_workbook(MSC)
    for master_type in EDITABLE:
        _master_tab(book, master_type)
    order = ["README", *(TABS[t] for t in EDITABLE), "Filetype"]
    book._sheets = [book[name] for name in order]  # noqa: SLF001 - openpyxl has no reorder API
    _readme(book)
    book.properties.creator = "SBAM"
    book.properties.lastModifiedBy = "SBAM"
    book.save(OUT)
    print(f"wrote {OUT.relative_to(ROOT)}: {book.sheetnames}")


if __name__ == "__main__":
    main()
