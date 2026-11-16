"""E3.2 contract additions: the Tooling master (Manufacturing Masters v0.2) and the engine DTOs."""

import uuid
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from pydantic import ValidationError
from sbam_contracts.canonical import sha256_of
from sbam_contracts.masters import (
    ADMIN_EDITABLE,
    MasterStatus,
    MasterType,
    RagReference,
    RulesPayload,
    ToolingPayload,
    extract_tooling_status,
    identify,
    is_restricted,
)
from sbam_contracts.masters_api import MasterEntryCreate, SnapshotEntry, snapshot_document
from sbam_contracts.masters_v03 import ToolingPayloadV03
from sbam_contracts.masters_workbook import (
    COLUMNS,
    TAB_ORDER,
    TOOLING_TAB,
    parse_workbook,
    validate_structure,
)

REPO = Path(__file__).resolve().parents[3]
# Masters 0.3 (#781): Tooling is the approved Tool Factory registry. The BAM 0.2 identity rules
# below stay tested: they are the migration's input.
WORKBOOK = REPO / "server/tests/fixtures/masters/masters_0_3.xlsx"

Sheets = dict[str, list[list[Any]]]


def load(path: Path) -> Sheets:
    book = openpyxl.load_workbook(path, data_only=False, read_only=True)
    try:
        return {
            ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in book.worksheets
        }
    finally:
        book.close()


@pytest.fixture(name="book")
def fixture_book() -> Sheets:
    return load(WORKBOOK)


# --- Tooling in the workbook layout --------------------------------------------------------------


def test_e3_2_tooling_columns_match_the_approved_headers(book: Sheets) -> None:
    header = [str(c).strip() for c in book[TOOLING_TAB][1] if c is not None]
    assert header[0] == "Tooling ID"
    assert header[1:] == [name for name, _ in COLUMNS[MasterType.TOOLING]]


def test_e3_2_workbook_without_tooling_still_validates(book: Sheets) -> None:
    without = {tab: grid for tab, grid in book.items() if tab != TOOLING_TAB}
    assert tuple(without) == TAB_ORDER
    assert validate_structure(without) == []


def test_e3_2_workbook_with_the_tooling_tab_parses_its_tools(book: Sheets) -> None:
    report = parse_workbook(book)
    assert report.errors == []
    tools = [r for r in report.rows if r.master_type == MasterType.TOOLING]
    assert [r.identity.stable_id for r in tools] == ["TF-01", "TOOL-MCP-01", "TOOL-API-01"]
    first = tools[0].payload
    assert isinstance(first, ToolingPayloadV03)
    assert (first.tool_name, first.category, first.bullm_badge) == (
        "Claude Code",
        "Agents",
        "BuLLM Recommended",
    )


def test_e3_2_tooling_tab_in_the_wrong_place_is_a_structure_error(book: Sheets) -> None:
    tooling = book[TOOLING_TAB]
    moved = {**{t: g for t, g in book.items() if t != TOOLING_TAB}, TOOLING_TAB: tooling}
    assert [e.path for e in validate_structure(moved)] == ["workbook!tabs"]


def test_e3_2_tooling_id_must_be_one_short_line(book: Sheets) -> None:
    grid = [list(r) for r in book[TOOLING_TAB]]
    grid[3][0] = "TF-01\nsecond line"
    report = parse_workbook({**book, TOOLING_TAB: grid})
    assert [e.path for e in report.errors] == ["Tooling!row4:colA"]


# --- Tooling identity -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("Category recorded; configuration TBD", MasterStatus.PROPOSED),
        ("", MasterStatus.PROPOSED),
        ("Approved by ops 2026-10-01", MasterStatus.APPROVED),
        ("retired", MasterStatus.RETIRED),
        ("Illustrative only", MasterStatus.ILLUSTRATIVE),
    ],
)
def test_e3_2_tooling_status_comes_from_its_own_status_column(
    status: str, expected: MasterStatus
) -> None:
    assert extract_tooling_status(status) is expected
    identity = identify(MasterType.TOOLING, "TOOL-MFG-12", ToolingPayload(record_status=status))
    assert identity.status is expected
    assert identity.stable_id == "TOOL-MFG-12"


def test_e3_2_payloads_never_carry_sbam_metadata() -> None:
    assert "status" not in ToolingPayload.model_fields  # the workbook's Status is record_status
    with pytest.raises(ValidationError):
        ToolingPayload.model_validate({"status": "PROPOSED"})


def test_e3_2_admin_editable_types_exclude_filetype() -> None:
    assert MasterType.TOOLING in ADMIN_EDITABLE
    assert MasterType.FILETYPE not in ADMIN_EDITABLE


@pytest.mark.parametrize(
    ("payload", "restricted"),
    [
        (ToolingPayload(security_access_level="Restricted - finance only"), True),
        (ToolingPayload(security_access_level="TBD"), False),
        (
            RulesPayload(
                rag_reference=RagReference.model_validate(
                    {
                        "Example": "",
                        "Links / Path": "",
                        "Entity / Node Type": "",
                        "Security / Access Level": "restricted",
                        "Refresh Frequency": "",
                        "ID Field": "",
                        "Parent Entity": "",
                    }
                )
            ),
            True,
        ),
        (RulesPayload(domain_rule="RULE-BOM: x"), False),
    ],
)
def test_e3_2_restricted_entries_are_detected(payload: Any, restricted: bool) -> None:
    assert is_restricted(payload) is restricted


# --- Engine DTOs ----------------------------------------------------------------------------------


def test_e3_2_snapshot_hash_ignores_entry_order() -> None:
    project = uuid.uuid4()
    a = SnapshotEntry(
        entry_id=uuid.uuid4(),
        master_type=MasterType.RULES,
        stable_id="RULE-BOM",
        version=2,
        status=MasterStatus.PROPOSED,
    )
    b = SnapshotEntry(
        entry_id=uuid.uuid4(),
        master_type=MasterType.SKILLS,
        stable_id="SK-BOM",
        version=1,
        status=MasterStatus.PROPOSED,
    )
    one = sha256_of(snapshot_document(project, [a, b]))
    two = sha256_of(snapshot_document(project, [b, a]))
    assert one == two and one.startswith("sha256:")
    bumped = a.model_copy(update={"version": 3})
    assert sha256_of(snapshot_document(project, [bumped, b])) != one


def test_e3_2_empty_snapshot_still_has_a_hash() -> None:
    assert sha256_of(snapshot_document(uuid.uuid4(), [])).startswith("sha256:")


def test_e3_2_entry_create_forbids_unknown_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        MasterEntryCreate.model_validate({"payload": {}, "status": "APPROVED"})
