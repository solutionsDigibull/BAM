"""Workbook layout and checks for the admin masters (BAM 0.2, TD §7.3.1, issue E0.4).

Everything here is pure: it works on cell grids that are already read (rows of cell values, row 1
first). Reading the .xlsx with openpyxl belongs to the importer (E3.3). Error paths look like
`Rules!row2:col9`: the tab, the sheet row, and the column number from the "Metadata" row 1
(Rules and Skills number their columns from 0, the others from 1). Column A is `colA`.
"""

import difflib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from importlib.resources import files
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sbam_contracts.api import DiffKind, ErrorCode, FieldError, StructureDiff
from sbam_contracts.masters import (
    README_MAX_COLUMNS,
    AgentsPayload,
    FiletypePayload,
    MasterIdentity,
    MasterPayload,
    MasterType,
    OrganizationPayload,
    RagReference,
    RulesPayload,
    SkillsPayload,
    ToolingPayload,
    cell_text,
    identify,
)

MAX_WORKBOOK_BYTES: Final = 15 * 1024 * 1024
# Zip-bomb guard (E3.3): the members may expand to 100 MB in total, at most 100x their packed size.
MAX_UNPACKED_BYTES: Final = 100 * 1024 * 1024
MAX_PACK_RATIO: Final = 100
SPREADSHEET_MAIN: Final = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)
FILETYPE_COUNT: Final = 43
README_TAB: Final = "README"
HEADER_ROWS: Final = 3  # Metadata, Name, Description; data starts on row 4
NAME_HEADER_ROW: Final = 2
TITLE_PREFIX: Final = "BuLLM - Admin Masters (BAM)"

TAB_ORDER: Final = (README_TAB, "Organization", "Rules", "Agents", "Skills", "Filetype")
# Manufacturing Masters v0.2 adds a Tooling tab between Skills and Filetype (E3.2). It is optional,
# so a BAM 0.2 workbook without it stays valid.
TOOLING_TAB: Final = "Tooling"
TAB_ORDER_WITH_TOOLING: Final = (*TAB_ORDER[:5], TOOLING_TAB, *TAB_ORDER[5:])
OPTIONAL_TABS: Final = frozenset({TOOLING_TAB})
TABS: Final = {
    MasterType.ORGANIZATION: "Organization",
    MasterType.RULES: "Rules",
    MasterType.AGENTS: "Agents",
    MasterType.SKILLS: "Skills",
    MasterType.TOOLING: TOOLING_TAB,
    MasterType.FILETYPE: "Filetype",
}
ROW_LABEL_PREFIX: Final = {
    MasterType.ORGANIZATION: "Unit",
    MasterType.RULES: "Rule",
    MasterType.AGENTS: "Agent",
    MasterType.SKILLS: "Skill",
    MasterType.TOOLING: "TOOL",
    MasterType.FILETYPE: "Filetype",
}
# Row labels are '<prefix> #n', except Tooling whose label is its stable id (TOOL-MFG-01).
TOOLING_LABEL: Final = re.compile(r"TOOL-[A-Z0-9][A-Z0-9-]*\s*")
# (column name in row 2, payload field). The order is the workbook order and is frozen.
COLUMNS: Final[dict[MasterType, tuple[tuple[str, str], ...]]] = {
    MasterType.ORGANIZATION: (
        ("Org Name", "org_name"),
        ("Type", "type"),
        ("Code", "code"),
        ("Location", "location"),
        ("Org Details", "org_details"),
        ("Employees", "employees"),
        ("Customers", "customers"),
        ("Offerings", "offerings"),
        ("Competencies", "competencies"),
        ("Data Sources", "data_sources"),
        ("Systems/Tools", "systems_tools"),
        ("Assets", "assets"),
        ("Certifications", "certifications"),
        ("Misc / Audit", "misc_audit"),
    ),
    MasterType.RULES: (
        ("Domain Rule", "domain_rule"),
        ("PCB Panelization", "pcb_panelization"),
        ("Components (BOM)", "components_bom"),
        ("Materials & Finish", "materials_finish"),
        ("Box Build", "box_build"),
        ("Machine HR Rates", "machine_hr_rates"),
        ("Outsourced Services", "outsourced_services"),
        ("Assembly Routing", "assembly_routing"),
        ("Rules (DFM/Sourcing)", "rules_dfm_sourcing"),
        ("Misc", "misc"),
        ("RAG Reference", "rag_reference"),
    ),
    MasterType.AGENTS: (
        ("Agent Name", "agent_name"),
        ("Description", "description"),
        ("Model", "model"),
        ("Repo", "repo"),
        ("API", "api"),
        ("MCP Server", "mcp_server"),
        ("Script", "script"),
        ("Prompt", "prompt"),
        ("Eval", "eval"),
        ("Logs", "logs"),
        ("Misc", "misc"),
    ),
    MasterType.SKILLS: (
        ("Skill", "skill"),
        ("Documents", "documents"),
        ("RFQ / Inquiries", "rfq_inquiries"),
        ("Currency", "currency"),
        ("Supplier Master", "supplier_master"),
        ("Commodity Pricing", "commodity_pricing"),
        ("Shipping & Logistics", "shipping_logistics"),
        ("Templates", "templates"),
        ("HITL Governance", "hitl_governance"),
        ("Misc", "misc"),
        ("RAG Reference", "rag_reference"),
    ),
    MasterType.TOOLING: (
        ("Category", "category"),
        ("Examples", "examples"),
        ("Links / Path", "links_path"),
        ("Owner / access", "owner_access"),
        ("Status", "record_status"),
        ("Source version / checked_at", "source_version_checked_at"),
        ("Search tag", "search_tag"),
        ("Tenant / account scope", "tenant_account_scope"),
        ("Entity / Node Type", "entity_node_type"),
        ("Security / Access Level", "security_access_level"),
        ("Refresh Frequency", "refresh_frequency"),
        ("Parent Entity", "parent_entity"),
        ("Source / evidence ref", "source_evidence_ref"),
        ("Binding / review state", "binding_review_state"),
        ("Search aliases / category", "search_aliases_category"),
    ),
    MasterType.FILETYPE: (
        ("File Format", "file_format"),
        ("Tier", "tier"),
        ("Purpose", "purpose"),
        ("RAG Ingestion", "rag_ingestion"),
        ("Example", "example"),
    ),
}
COLUMN_NUMBER_START: Final = {
    MasterType.ORGANIZATION: 1,
    MasterType.RULES: 0,
    MasterType.AGENTS: 1,
    MasterType.SKILLS: 0,
    MasterType.TOOLING: 1,
    MasterType.FILETYPE: 1,
}
PayloadModel = (
    type[OrganizationPayload]
    | type[RulesPayload]
    | type[AgentsPayload]
    | type[SkillsPayload]
    | type[ToolingPayload]
    | type[FiletypePayload]
)
PAYLOAD_MODELS: Final[dict[MasterType, PayloadModel]] = {
    MasterType.ORGANIZATION: OrganizationPayload,
    MasterType.RULES: RulesPayload,
    MasterType.AGENTS: AgentsPayload,
    MasterType.SKILLS: SkillsPayload,
    MasterType.TOOLING: ToolingPayload,
    MasterType.FILETYPE: FiletypePayload,
}

Grid = Sequence[Sequence[Any]]  # rows of cell values, row 1 first; None or "" for empty cells


class MasterRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    master_type: MasterType
    row_label: str
    sheet_row: int = Field(ge=1)
    payload: MasterPayload
    identity: MasterIdentity


class WorkbookReport(BaseModel):
    """Result of checking a workbook. `code` is set whenever `errors` is not empty."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode | None = None
    errors: list[FieldError] = Field(default_factory=list)
    rows: list[MasterRow] = Field(default_factory=list)


def check_workbook_container(
    filename: str, size_bytes: int, member_names: Iterable[str]
) -> list[FieldError]:
    """File-level checks before any cell is read: .xlsx only (no macros), at most 15 MB."""
    errors: list[FieldError] = []
    if not filename.lower().endswith(".xlsx"):
        errors.append(FieldError(path="file", message="only .xlsx workbooks are accepted"))
    if any(name.lower().endswith("vbaproject.bin") for name in member_names):
        errors.append(FieldError(path="file", message="workbooks with macros are not accepted"))
    if size_bytes > MAX_WORKBOOK_BYTES:
        errors.append(FieldError(path="file", message="workbook is larger than 15 MB"))
    return errors


def frozen_filetype() -> list[FiletypePayload]:
    """The 43 frozen Filetype rows of BAM 0.2, #1 first. Only super-admin changes them."""
    data = files("sbam_contracts").joinpath("masters_filetype.json").read_text(encoding="utf-8")
    return [FiletypePayload.model_validate(row) for row in json.loads(data)]


def check_archive(members: Iterable[tuple[str, int, int]]) -> FieldError | None:
    """Zip-bomb guard from the zip directory alone (name, unpacked size, packed size), before any
    member is inflated: at most 100 MB unpacked in total and a ratio of at most 100."""
    unpacked = packed = 0
    for _, size, compressed in members:
        unpacked += size
        packed += compressed
    if unpacked > MAX_UNPACKED_BYTES:
        return FieldError(path="file", message="workbook expands beyond 100 MB")
    if unpacked > MAX_PACK_RATIO * max(packed, 1):
        return FieldError(path="file", message="workbook compression ratio is above 100")
    return None


def check_content_types(content_types_xml: str) -> FieldError | None:
    """An .xlsx declares the spreadsheetml main part in an `Override`; macro-enabled or other OOXML
    files don't. Comments and other text don't count as declarations."""
    not_xlsx = FieldError(path="file", message="the file is not an Excel .xlsx workbook")
    if "<!DOCTYPE" in content_types_xml:  # [Content_Types].xml never has one; no entity games
        return not_xlsx
    try:
        root = ET.fromstring(content_types_xml)  # noqa: S314 - no DOCTYPE (above), size-capped
    except ET.ParseError:
        return not_xlsx
    declared = [
        el.get("ContentType", "") for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "Override"
    ]
    if any("macroEnabled" in ct for ct in declared):
        return FieldError(path="file", message="workbooks with macros are not accepted")
    if SPREADSHEET_MAIN not in declared:
        return not_xlsx
    return None


def _header_diffs(
    expected: list[str], actual: list[str]
) -> list[tuple[int, str | None, str | None, DiffKind]]:
    """(position, want, got, kind) per header mismatch. The two sequences are aligned first, so
    one inserted or dropped column is one ADDED / REMOVED, not a run of shifted columns."""
    found: list[tuple[int, str | None, str | None, DiffKind]] = []
    matcher = difflib.SequenceMatcher(a=expected, b=actual, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        wants, gots = expected[i1:i2], actual[j1:j2]
        for k in range(max(len(wants), len(gots))):
            want = wants[k] if k < len(wants) else None
            got = gots[k] if k < len(gots) else None
            if want is None:
                kind = DiffKind.ADDED
            elif got is None:
                kind = DiffKind.REMOVED
            elif got in expected and want in actual:
                kind = DiffKind.REORDERED
            else:
                kind = DiffKind.RENAMED
            found.append((j1 + k if got is not None else i1 + k, want, got, kind))
    # A column that is both "added" somewhere and "removed" elsewhere was moved.
    moved = {g for _, w, g, kd in found if kd is DiffKind.ADDED} & {
        w for _, w, g, kd in found if kd is DiffKind.REMOVED
    }
    return [
        (i, w, g, DiffKind.REORDERED if (g or w) in moved and None in (w, g) else kd)
        for i, w, g, kd in found
    ]


def _col_path(master_type: MasterType, row: int, index: int) -> str:
    return f"{TABS[master_type]}!row{row}:col{COLUMN_NUMBER_START[master_type] + index}"


def _cells(grid: Grid, row: int) -> Sequence[Any]:
    return grid[row - 1] if 0 < row <= len(grid) else ()


def _header_problem(want: str | None, got: str | None) -> str:
    if want is None:
        return f"unexpected column {got!r}"
    if got is None:
        return f"missing column {want!r}"
    return f"expected {want!r}, found {got!r}"


def _validate_headers(master_type: MasterType, grid: Grid) -> list[FieldError]:
    tab = TABS[master_type]
    headers = [cell_text(c).strip() for c in _cells(grid, NAME_HEADER_ROW)]
    while headers and not headers[-1]:
        headers.pop()
    errors: list[FieldError] = []
    if not headers or headers[0] != "Name":
        errors.append(FieldError(path=f"{tab}!row2:colA", message="column A must be 'Name'"))
    expected = [name for name, _ in COLUMNS[master_type]]
    actual = headers[1:]
    for index, want, got, kind in _header_diffs(expected, actual):
        path = _col_path(master_type, NAME_HEADER_ROW, index)
        diff = StructureDiff(tab=tab, expected=want, actual=got, kind=kind)
        errors.append(FieldError(path=path, message=_header_problem(want, got), diff=diff))
    return errors


def _validate_filetype_labels(grid: Grid) -> list[FieldError]:
    entries = []
    for row in range(HEADER_ROWS + 1, len(grid) + 1):
        cells = _cells(grid, row)
        label = cell_text(cells[0] if cells else None).strip()
        if label and not label.startswith("Note:"):  # the tab ends with a note row
            entries.append((row, label))
    expected = [f"Filetype #{n}" for n in range(1, FILETYPE_COUNT + 1)]
    if [label for _, label in entries] == expected:
        return []
    first_bad = next(
        (r for (r, got), want in zip(entries, expected, strict=False) if got != want), None
    )
    row = first_bad or (entries[-1][0] if entries else HEADER_ROWS + 1)
    message = f"Filetype must hold exactly {FILETYPE_COUNT} rows labelled Filetype #1 to #43"
    return [FieldError(path=f"Filetype!row{row}:colA", message=message)]


def validate_readme(grid: Grid) -> list[FieldError]:
    errors: list[FieldError] = []
    first = _cells(grid, 1)
    if not cell_text(first[0] if first else None).startswith(TITLE_PREFIX):
        message = "README must start with the workbook title"
        errors.append(FieldError(path="README!row1:colA", message=message))
    for row, cells in enumerate(grid, start=1):
        if any(cell_text(c) for c in cells[README_MAX_COLUMNS:]):
            errors.append(
                FieldError(path=f"README!row{row}", message="README holds at most 4 columns")
            )
    return errors


def validate_structure(sheets: Mapping[str, Grid]) -> list[FieldError]:
    """The frozen structure: tabs, column names and order, Filetype labels. Never auto-maps."""
    if tuple(sheets) not in (TAB_ORDER, TAB_ORDER_WITH_TOOLING):
        missing = [t for t in TAB_ORDER if t not in sheets]
        extra = [t for t in sheets if t not in TAB_ORDER_WITH_TOOLING]  # Tooling is permitted
        detail = (
            f"missing {missing}, unexpected {extra}" if missing or extra else "tabs are reordered"
        )
        message = f"expected tabs {list(TAB_ORDER)}; {detail}"
        if missing:
            diff = StructureDiff(
                tab="workbook", expected=missing[0], actual=None, kind=DiffKind.REMOVED
            )
        elif extra:
            diff = StructureDiff(
                tab="workbook", expected=None, actual=extra[0], kind=DiffKind.ADDED
            )
        else:
            order = ", ".join(sheets)
            want = TAB_ORDER_WITH_TOOLING if "Tooling" in sheets else TAB_ORDER
            diff = StructureDiff(
                tab="workbook", expected=", ".join(want), actual=order, kind=DiffKind.REORDERED
            )
        return [FieldError(path="workbook!tabs", message=message, diff=diff)]
    errors: list[FieldError] = []
    for master_type, tab in TABS.items():
        if tab in sheets:
            errors += _validate_headers(master_type, sheets[tab])
    errors += _validate_filetype_labels(sheets["Filetype"])
    errors += validate_readme(sheets[README_TAB])
    return errors


def parse_rag_cell(text: str) -> RagReference | None:
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("RAG Reference is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("RAG Reference must be a JSON object with 7 keys")
    try:
        return RagReference.model_validate(data)
    except ValidationError as exc:
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"RAG Reference needs exactly the 7 documented keys ({detail})") from None


def _tier(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float | int) and float(value).is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _error_path(master_type: MasterType, row: int, names: list[str], loc: Sequence[Any]) -> str:
    field = str(loc[0]) if loc else ""
    if field in names:
        return _col_path(master_type, row, names.index(field))
    return f"{TABS[master_type]}!row{row}"


def _row_values(
    master_type: MasterType, sheet_row: int, body: list[Any], texts: list[str]
) -> tuple[dict[str, Any], list[FieldError]]:
    values: dict[str, Any] = {}
    errors: list[FieldError] = []
    for index, ((_, field), text) in enumerate(zip(COLUMNS[master_type], texts, strict=True)):
        if field == "rag_reference":
            try:
                values[field] = parse_rag_cell(text)
            except ValueError as exc:
                path = _col_path(master_type, sheet_row, index)
                errors.append(FieldError(path=path, message=str(exc)))
        elif field == "tier":
            values[field] = _tier(body[index] if index < len(body) else None)
        else:
            values[field] = text
    return values, errors


def _parse_row(
    master_type: MasterType, sheet_row: int, cells: Sequence[Any]
) -> tuple[MasterRow | None, list[FieldError]]:
    tab = TABS[master_type]
    columns = COLUMNS[master_type]
    label = cell_text(cells[0] if cells else None)
    body = list(cells[1 : 1 + len(columns)])
    texts = [cell_text(c) for c in body] + [""] * (len(columns) - len(body))
    if label.strip().startswith("Note:") or (not label.strip() and not any(texts)):
        return None, []  # tab note or empty row
    label_path = f"{tab}!row{sheet_row}:colA"
    if not label.strip():
        return None, [FieldError(path=label_path, message="row label is missing")]
    prefix = ROW_LABEL_PREFIX[master_type]
    if master_type == MasterType.TOOLING:
        if not TOOLING_LABEL.fullmatch(label):
            message = "row label must be a Tooling id like 'TOOL-MFG-01'"
            return None, [FieldError(path=label_path, message=message)]
    elif not re.fullmatch(rf"{prefix} #\d+\s*", label):
        message = f"row label must look like '{prefix} #n'"
        return None, [FieldError(path=label_path, message=message)]
    if not any(texts):
        return None, []  # label only: a blank template row, not an entry
    values, errors = _row_values(master_type, sheet_row, body, texts)
    if errors:
        return None, errors
    names = [field for _, field in columns]
    try:
        payload = PAYLOAD_MODELS[master_type].model_validate(values)
    except ValidationError as exc:
        return None, [
            FieldError(
                path=_error_path(master_type, sheet_row, names, e["loc"]), message=str(e["msg"])
            )
            for e in exc.errors()
        ]
    try:
        identity = identify(master_type, label, payload)
    except ValueError as exc:
        misc_index = next(i for i, name in enumerate(names) if name in ("misc", "misc_audit"))
        path = _col_path(master_type, sheet_row, misc_index)
        return None, [FieldError(path=path, message=str(exc))]
    row = MasterRow(
        master_type=master_type,
        row_label=label,
        sheet_row=sheet_row,
        payload=payload,
        identity=identity,
    )
    return row, []


def parse_workbook(sheets: Mapping[str, Grid]) -> WorkbookReport:
    """Validate the structure, then each data row of the masters. Stops on structure errors."""
    structure = validate_structure(sheets)
    if structure:
        return WorkbookReport(code=ErrorCode.STRUCTURE_MISMATCH, errors=structure)
    rows: list[MasterRow] = []
    errors: list[FieldError] = []
    for master_type, tab in TABS.items():
        if tab not in sheets:
            continue  # an optional tab (Tooling) that this workbook doesn't have
        seen: set[str] = set()
        grid = sheets[tab]
        for sheet_row in range(HEADER_ROWS + 1, len(grid) + 1):
            row, row_errors = _parse_row(master_type, sheet_row, grid[sheet_row - 1])
            errors += row_errors
            if row is None:
                continue
            if row.identity.stable_id in seen:
                message = f"duplicate stable id {row.identity.stable_id}"
                errors.append(FieldError(path=f"{tab}!row{sheet_row}:colA", message=message))
            seen.add(row.identity.stable_id)
            rows.append(row)
    code = ErrorCode.VALIDATION_ERROR if errors else None
    return WorkbookReport(code=code, errors=errors, rows=rows)
