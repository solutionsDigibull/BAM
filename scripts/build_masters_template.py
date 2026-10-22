#!/usr/bin/env python3
"""Build the frozen BAM 0.2 workbook layout used by the masters export and template (E3.4, #57).

    cd server && uv run python ../scripts/build_masters_template.py

Writes `server/api/sbam_api/masters/template.xlsx` from the two reference workbooks:
  * the MSC one (`docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx`): README,
    header rows 1-3 and styling of every tab, the five tenant tabs emptied from row 4 on, the 43
    frozen Filetype rows kept (platform data the importer requires);
  * Manufacturing Masters v0.2: header rows 1-3 of the Tooling tab, placed before Filetype and
    styled like the Skills tab. The export drops it when a tenant has no Tooling entries.

Re-run it only when the frozen structure changes; commit the result.
"""

from copy import copy
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
MSC = ROOT / "docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx"
V02 = ROOT / "docs/reference/ui/admin-masters/BuLLM - Manufacturing Masters v0.2.xlsx"
OUT = ROOT / "server/api/sbam_api/masters/template.xlsx"
TENANT_TABS = ("Organization", "Rules", "Agents", "Skills")
HEADER_ROWS = 3


def main() -> None:
    book = openpyxl.load_workbook(MSC)
    for tab in TENANT_TABS:
        sheet = book[tab]
        if sheet.max_row > HEADER_ROWS:
            sheet.delete_rows(HEADER_ROWS + 1, sheet.max_row - HEADER_ROWS)

    source = openpyxl.load_workbook(V02, read_only=True)["Tooling"]
    headers = [list(r) for r in source.iter_rows(max_row=HEADER_ROWS, values_only=True)]
    tooling = book.copy_worksheet(book["Skills"])  # Skills' header styling, widths, freeze panes
    tooling.title = "Tooling"
    tooling.freeze_panes = book["Skills"].freeze_panes  # copy_worksheet leaves it out
    for row in tooling.iter_rows():
        for cell in row:
            cell.value = None
    last = tooling.max_column  # styles past Skills' last column repeat its last one
    for r, values in enumerate(headers, start=1):
        for c, value in enumerate(values, start=1):
            cell = tooling.cell(r, c, value)
            if c > last:
                cell._style = copy(tooling.cell(r, last)._style)
    book.move_sheet(tooling, offset=book.sheetnames.index("Filetype") - book.index(tooling))

    book.properties.creator = "SBAM"
    book.properties.lastModifiedBy = "SBAM"
    book.save(OUT)
    print(f"wrote {OUT.relative_to(ROOT)}: {book.sheetnames}")


if __name__ == "__main__":
    main()
