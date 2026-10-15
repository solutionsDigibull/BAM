"""E3.5: stable-id and status extraction, and writing a status back into the row."""

from pathlib import Path
from typing import Any

import openpyxl
import pytest
from sbam_contracts.masters import (
    AgentsPayload,
    MasterStatus,
    MasterType,
    RulesPayload,
    SourceKind,
    ToolingPayload,
    extract_status,
    extract_tooling_status,
    identify,
    replace_status,
    replace_tooling_status,
    with_status,
)
from sbam_contracts.masters_workbook import COLUMNS, TABS, parse_workbook

REPO = Path(__file__).resolve().parents[3]
WORKBOOK = REPO / "docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx"
Sheets = dict[str, list[list[Any]]]


def load() -> Sheets:
    book = openpyxl.load_workbook(WORKBOOK, data_only=False, read_only=True)
    try:
        return {
            ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in book.worksheets
        }
    finally:
        book.close()


# --- T01-T05: extraction -------------------------------------------------------------------------


def test_e3_5_t01_rules_stable_id_from_the_domain_rule_prefix() -> None:
    payload = RulesPayload(domain_rule="RULE-BOM: applies to BOM. Policy: compare costed BOM")
    assert identify(MasterType.RULES, "Rule #2", payload).stable_id == "RULE-BOM"


def test_e3_5_t02_agents_stable_id_from_the_description_prefix() -> None:
    payload = AgentsPayload(description="AG-INTAKE. Allowed input: RFQ/scenario")
    assert identify(MasterType.AGENTS, "Agent #2", payload).stable_id == "AG-INTAKE"


def test_e3_5_t03_sample_row_falls_back_to_its_label_and_is_illustrative() -> None:
    identity = identify(
        MasterType.AGENTS, "Agent #1", AgentsPayload(description="Parses BOM files")
    )
    assert identity.stable_id == "Agent #1"
    assert identity.source_kind is SourceKind.BAM_0_2_SAMPLE
    assert identity.status is MasterStatus.ILLUSTRATIVE


def test_e3_5_t04_status_parsed_from_the_misc_fragment() -> None:
    assert extract_status("Cost override x. Status: Proposed") is MasterStatus.PROPOSED
    assert extract_status("") is MasterStatus.PROPOSED  # missing means PROPOSED


def test_e3_5_t05_unknown_status_is_a_row_error() -> None:
    with pytest.raises(ValueError, match="unknown status Draft"):
        extract_status("Status: Draft")


def test_e3_5_every_consolidated_row_gets_a_stable_id_and_status() -> None:
    report = parse_workbook(load())
    assert report.errors == []
    assert report.rows and all(r.identity.stable_id and r.identity.status for r in report.rows)


# --- write-back ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("misc", "status", "expected"),
    [
        (
            "Cost override x. Status: Proposed",
            MasterStatus.APPROVED,
            "Cost override x. Status: Approved",
        ),
        (
            "Cost override x. Status: Proposed.",
            MasterStatus.RETIRED,
            "Cost override x. Status: Retired.",
        ),
        ("a;  status:   proposed  ", MasterStatus.APPROVED, "a;  status:   Approved  "),
        ("", MasterStatus.APPROVED, "Status: Approved"),
        ("   ", MasterStatus.APPROVED, "Status: Approved"),
        (
            "Search tag: mfg-rule-01",
            MasterStatus.APPROVED,
            "Search tag: mfg-rule-01; Status: Approved",
        ),
        ("Reviewed by QA.", MasterStatus.APPROVED, "Reviewed by QA. Status: Approved"),
        ("Reviewed by QA.\n", MasterStatus.APPROVED, "Reviewed by QA. Status: Approved\n"),
    ],
)
def test_e3_5_only_the_status_fragment_changes(
    misc: str, status: MasterStatus, expected: str
) -> None:
    written = replace_status(misc, status)
    assert written == expected
    assert extract_status(written) is status


def test_e3_5_two_status_fragments_refuse_a_write_back() -> None:
    with pytest.raises(ValueError, match="multiple Status fragments"):
        replace_status("Status: Proposed; Status: Approved", MasterStatus.RETIRED)


def test_e3_5_rest_of_misc_is_byte_for_byte_identical() -> None:
    misc = "Standard Scrap Rate: 1.5%; Search tag: mfg-rule-01 — ünïcödé. Status: Proposed"
    written = replace_status(misc, MasterStatus.APPROVED)
    assert written.removesuffix("Approved") == misc.removesuffix("Proposed")


@pytest.mark.parametrize(
    ("current", "status", "expected"),
    [
        (
            "Category recorded; configuration TBD",
            MasterStatus.APPROVED,
            "Approved; Category recorded; configuration TBD",
        ),
        ("Proposed - waiting for owner", MasterStatus.APPROVED, "Approved - waiting for owner"),
        ("", MasterStatus.RETIRED, "Retired"),
    ],
)
def test_e3_5_tooling_status_column_write_back(
    current: str, status: MasterStatus, expected: str
) -> None:
    written = replace_tooling_status(current, status)
    assert written == expected
    assert extract_tooling_status(written) is status


def test_e3_5_with_status_touches_only_the_status_field() -> None:
    rule = RulesPayload(domain_rule="RULE-BOM: x", misc="Cost override x. Status: Proposed")
    approved = with_status(rule, MasterStatus.APPROVED)
    assert isinstance(approved, RulesPayload)
    assert approved.misc == "Cost override x. Status: Approved"
    assert approved.model_dump(exclude={"misc"}) == rule.model_dump(exclude={"misc"})
    tool = with_status(ToolingPayload(record_status="Category recorded"), MasterStatus.RETIRED)
    assert isinstance(tool, ToolingPayload) and tool.record_status == "Retired; Category recorded"


def test_e3_5_t07_round_trip_after_a_status_change_differs_in_one_cell_only() -> None:
    """Write a status back into a real workbook row, re-parse the workbook with the importer's
    parser: 0 errors, and only that row's Misc cell and status changed (the full export -> import
    file round trip lands with #56 / #57)."""
    sheets = load()
    before = parse_workbook(sheets)
    target = next(
        r
        for r in before.rows
        if r.master_type == MasterType.RULES and r.identity.source_kind != SourceKind.BAM_0_2_SAMPLE
    )
    assert isinstance(target.payload, RulesPayload)
    new_misc = replace_status(target.payload.misc, MasterStatus.APPROVED)
    tab = TABS[MasterType.RULES]
    misc_col = 1 + [f for _, f in COLUMNS[MasterType.RULES]].index("misc")
    edited = {name: [list(row) for row in grid] for name, grid in sheets.items()}
    edited[tab][target.sheet_row - 1][misc_col] = new_misc

    after = parse_workbook(edited)
    assert after.errors == []
    changed = [
        (a.row_label, a.identity.status)
        for a, b in zip(after.rows, before.rows, strict=True)
        if a != b
    ]
    assert changed == [(target.row_label, MasterStatus.APPROVED)]
