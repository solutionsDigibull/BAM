#!/usr/bin/env python3
"""Build Masters 0.3 workbooks (#781): the synthetic test fixture, or a tenant seed.

    cd server && uv run python ../scripts/build_masters_reference_0_3.py fixture
    cd server && uv run python ../scripts/build_masters_reference_0_3.py seed \
        <Admin_Masters>/src/workbook.json <out.xlsx>

Both start from the export template (`template.xlsx`), so they import as they are.

* `fixture` writes `server/tests/fixtures/masters/masters_0_3.xlsx`: synthetic rows only (the
  fixture pack holds no real client data, see its README), covering every master and the cases
  the contract tests need (an Assistant, Active / Archived statuses, a Restricted tool, text that
  looks like a formula, a multi-line cell).
* `seed` fills the template with the manager-approved Admin Masters data (Organization, Rules,
  Agents, Skills, Tools -> Tooling) for an Admin to import. It holds real client data: write it
  outside the repository and never commit it. The approved data repeats three Tools ids
  (TOOL-MKTG-04, -08, -161); a repeat gets a visible `-DUP<n>` suffix so the import accepts it.
"""

import json
import sys
from pathlib import Path

import openpyxl
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_v03 import COLUMNS
from sbam_contracts.masters_workbook import HEADER_ROWS, TABS

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "server/api/sbam_api/masters/template.xlsx"
FIXTURE = ROOT / "server/tests/fixtures/masters/masters_0_3.xlsx"
SHEETS = {
    "Organization": MasterType.ORGANIZATION,
    "Rules": MasterType.RULES,
    "Agents": MasterType.AGENTS,
    "Skills": MasterType.SKILLS,
    "Tools": MasterType.TOOLING,
}
# Synthetic rows in the approved column order (Alpha Dynamics Ltd, example.com).
SYNTHETIC: dict[MasterType, list[list[str]]] = {
    MasterType.ORGANIZATION: [
        ["ORG-HQ-01", "Alpha Dynamics Ltd - HQ", "Website: https://alpha.example.com", "1 Test Road, Springfield", "ISO 9001:2015", "Precision machining", "Customers: OEMs", "QA lead (approver)", "https://docs.example.com/alpha/quality", "SYS-ERP-01", "AST-CNC-01", "Synthetic fixture", "", ""],
        ["ORG-BU-01", "Alpha Dynamics Ltd - Unit I", "", "2 Test Road, Springfield", "", "Box build", "", "", "", "", "", "", "", ""],
    ],
    MasterType.RULES: [
        ["RULE-POLICY-BOM", "Rule / Type: Policy / BOM; Description: Compare source and costed BOM", "Owner sign-off v1", "Org: ORG-HQ-01. Skills: SK-01", "Stop on unresolved MPN", "QA lead / v1", "Active", "2026-11-01 / 2027-11-01", "SK-01"],
        ["RULE-CHECK-DFM", "Rule / Type: Validation / DFM; Description: Check panel rails", "", "Org: ORG-BU-01. Skills: SK-02", "", "", "Proposed starter; not approved", "", "SK-02.Panel-Rails"],
        ["RULE-OLD-01", "Retired example", "", "Org: ORG-HQ-01", "", "", "Archived", "", ""],
    ],
    MasterType.AGENTS: [
        ["AG-01", "RFQ Intake Agent", "Agent", "Internal", "", "Built in BuLLM", "Email parser", "Claude Sonnet"],
        ["AS-01", "Quality Assistant", "Assistant", "Internal", "Cuts across Rules and Skills", "Built in BuLLM", "", ""],
        ["AG-02", "Supplier Lookup Agent", "Agent", "External", "https://github.com/example/supplier-agent", "Imported · GitHub", "", ""],
    ],
    MasterType.SKILLS: [
        ["SK-01", "RFQ Intake", "Read the RFQ email", "Inbox", "On new email", "Python; FastAPI", "rfq_id, qty", "Parsed RFQ", "SK-02", "ORG-HQ-01; TF-01"],
        ["SK-02", "Costing", "Cost the BOM\nShow each step", "Parsed RFQ", "After SK-01", "Python", "cost", "Quote draft", "", ""],
    ],
    MasterType.TOOLING: [
        ["TF-01", "Claude Code", "Agents", "https://github.com/example/claude-code-tools", "Platform team", "Active", "v1 / 2026-11-01", "Monthly", "Agent", "Leaderboard", "BuLLM Recommended"],
        ["TOOL-MCP-01", "Reference MCP servers", "MCP", "https://github.com/example/mcp-servers", "Restricted - platform admins", "Proposed", "", "Quarterly", "MCP server", "Repo Library", ""],
        ["TOOL-API-01", "=HYPERLINK(\"https://example.com\")", "API", "TBD", "", "", "", "", "API", "Tech Stack", "Gap"],
    ],
}  # fmt: skip


def _write(book: openpyxl.Workbook, master_type: MasterType, rows: list[list[str]]) -> None:
    sheet = book[TABS[master_type]]
    for row_number, values in enumerate(rows, start=HEADER_ROWS + 1):
        for column, value in enumerate(values, start=1):
            if value:
                cell = sheet.cell(row_number, column, value)
                cell.data_type = "s"  # literal text, never a formula


def fixture() -> None:
    book = openpyxl.load_workbook(TEMPLATE)
    for master_type, rows in SYNTHETIC.items():
        _write(book, master_type, rows)
    book.save(FIXTURE)
    print(f"wrote {FIXTURE.relative_to(ROOT)}")


def seed(source: Path, out: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    book = openpyxl.load_workbook(TEMPLATE)
    for sheet in data["sheets"]:
        master_type = SHEETS.get(sheet["name"])
        if master_type is None:
            continue
        headers = [header for _, header, _ in COLUMNS[master_type]]
        index = {h: i for i, h in enumerate(sheet["headers"])}
        missing = [h for h in headers if h not in index]
        if missing:
            raise SystemExit(f"{sheet['name']}: approved data lacks {missing}")
        seen: dict[str, int] = {}
        rows = []
        for row in sheet["rows"]:
            values = [row["values"][index[h]] for h in headers]
            ident = values[0].strip()
            seen[ident] = seen.get(ident, 0) + 1
            if seen[ident] > 1:
                values[0] = f"{ident}-DUP{seen[ident]}"
            rows.append(values)
        _write(book, master_type, rows)
    book.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["fixture"]:
        fixture()
    elif sys.argv[1:2] == ["seed"] and len(sys.argv) == 4:
        seed(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit(__doc__)
