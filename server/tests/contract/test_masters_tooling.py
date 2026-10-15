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
    SourceKind,
    ToolingPayload,
    extract_tooling_status,
    identify,
    is_restricted,
)
from sbam_contracts.masters_api import MasterEntryCreate, SnapshotEntry, snapshot_document
from sbam_contracts.masters_workbook import (
    COLUMNS,
    TAB_ORDER,
    TOOLING_TAB,
    parse_workbook,
    validate_structure,
)

REPO = Path(__file__).resolve().parents[3]
BAM_0_2 = REPO / "docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx"
MFG_V0_2 = REPO / "docs/reference/ui/admin-masters/BuLLM - Manufacturing Masters v0.2.xlsx"

Sheets = dict[str, list[list[Any]]]


def load(path: Path) -> Sheets:
    book = openpyxl.load_workbook(path, data_only=False, read_only=True)
    try:
        return {
            ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in book.worksheets
        }
    finally:
        book.close()


def with_tooling(sheets: Sheets, tooling: list[list[Any]]) -> Sheets:
    """The BAM 0.2 workbook with a Tooling tab between Skills and Filetype."""
    out: Sheets = {}
    for tab in TAB_ORDER:
        if tab == "Filetype":
            out[TOOLING_TAB] = tooling
        out[tab] = sheets[tab]
    return out


@pytest.fixture(name="mfg_tooling", scope="module")
def fixture_mfg_tooling() -> list[list[Any]]:
    return load(MFG_V0_2)[TOOLING_TAB]


@pytest.fixture(name="bam")
def fixture_bam() -> Sheets:
    return load(BAM_0_2)


# --- Tooling in the workbook layout --------------------------------------------------------------


def test_e3_2_tooling_columns_match_the_v0_2_workbook_row_2(mfg_tooling: list[list[Any]]) -> None:
    header = [str(c).strip() for c in mfg_tooling[1] if c is not None]
    assert header[0] == "Name"
    assert header[1:] == [name for name, _ in COLUMNS[MasterType.TOOLING]]


def test_e3_2_workbook_without_tooling_still_validates(bam: Sheets) -> None:
    assert validate_structure(bam) == []


def test_e3_2_workbook_with_the_v0_2_tooling_tab_parses_eleven_tools(
    bam: Sheets, mfg_tooling: list[list[Any]]
) -> None:
    report = parse_workbook(with_tooling(bam, mfg_tooling))
    assert report.errors == []
    tools = [r for r in report.rows if r.master_type == MasterType.TOOLING]
    assert [r.identity.stable_id for r in tools] == [f"TOOL-MFG-{n:02d}" for n in range(1, 12)]
    assert {r.identity.status for r in tools} == {MasterStatus.PROPOSED}  # "Category recorded; …"
    assert {r.identity.source_kind for r in tools} == {SourceKind.TEMPLATE_ROW}
    first = tools[0].payload
    assert isinstance(first, ToolingPayload)
    assert (first.category, first.examples) == ("MCP", "WebMCP")


def test_e3_2_tooling_tab_in_the_wrong_place_is_a_structure_error(
    bam: Sheets, mfg_tooling: list[list[Any]]
) -> None:
    moved = {**bam, TOOLING_TAB: mfg_tooling}  # after Filetype
    assert [e.path for e in validate_structure(moved)] == ["workbook!tabs"]


def test_e3_2_tooling_row_label_must_be_a_tool_id(
    bam: Sheets, mfg_tooling: list[list[Any]]
) -> None:
    grid = [list(r) for r in mfg_tooling]
    grid[3][0] = "Tool #1"
    report = parse_workbook(with_tooling(bam, grid))
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
        entry_id=uuid.uuid4(), master_type=MasterType.RULES, stable_id="RULE-BOM", version=2
    )
    b = SnapshotEntry(
        entry_id=uuid.uuid4(), master_type=MasterType.SKILLS, stable_id="SK-BOM", version=1
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
