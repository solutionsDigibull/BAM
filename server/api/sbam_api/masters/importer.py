"""Masters workbook import (TD §8.2-§8.3, E3.3): `POST /admin/masters/import`.

The steps, each one before anything later can run:
  1. at most 15 MB (413 FILE_TOO_LARGE), sha256 of the bytes;
  2. ClamAV scan of the raw bytes: infected = 422 MALWARE (the bytes go to `quarantine/` through
     StorageBackend, audited with the signature and the key, never parsed), no verdict = 503
     SCAN_UNAVAILABLE;
  3. container checks from the zip directory alone: a real .xlsx (415 UNSUPPORTED_TYPE, macros
     included) and the zip-bomb limits (422 ARCHIVE_LIMIT);
  4. openpyxl in read-only, values-only mode (formulas are never evaluated, links never followed);
  5. the frozen structure (422 STRUCTURE_MISMATCH with a diff per mismatch), then every row;
  6. the Filetype tab must equal the frozen 43 rows (403 FILETYPE_FROZEN);
  7. `upsert` without row errors writes, in one transaction, a new entry per new stable id and a new
     version per changed payload. Anything else writes nothing (`applied: false`).

ponytail: the report is answered synchronously (a 15 MB read-only workbook parses in seconds, in the
threadpool); move it to a Temporal activity if workbooks grow.
"""

import hashlib
import io
import re
import uuid
import zipfile
from collections import Counter
from typing import Any

import openpyxl
from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.masters import FiletypePayload, MasterType
from sbam_contracts.masters_api import ImportMode, ImportReport, ImportRowError, ImportTabReport
from sbam_contracts.masters_workbook import (
    COLUMN_NUMBER_START,
    COLUMNS,
    HEADER_ROWS,
    MAX_WORKBOOK_BYTES,
    TAB_ORDER,
    TAB_ORDER_WITH_TOOLING,
    TABS,
    Grid,
    MasterRow,
    WorkbookReport,
    check_archive,
    check_content_types,
    check_workbook_container,
    parse_workbook,
    validate_structure,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.bulkhead import CLAMD, BulkheadFullError
from sbam_api.core.conventions import ApiError
from sbam_api.core.settings import Settings
from sbam_api.db.repository import StaleRowVersion
from sbam_api.filetypes.repository import current_payloads
from sbam_api.masters.documents import TENANT_SCOPE_PROJECT, scan_unavailable
from sbam_api.masters.models import MasterEntry, MasterEntryVersion
from sbam_api.masters.repository import MasterEntryRepository
from sbam_api.masters.service import _active_project, _audit, _correlation, _repo, version_conflict
from sbam_api.scan.clamd import ClamdClient, ClamdError
from sbam_api.storage import Kind, StorageBackend, StorageScope

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# The real tabs hold 10 to 160 rows. A tab with more rows than this is not a masters workbook, and
# is refused while it streams, before anything but its header is kept (A-19: a 14 MB, 400k-row tab
# took 11 s and 250 MB of the API process to be rejected after it was read whole).
MAX_DATA_ROWS = 10_000
_MAX_SCAN_ROWS = 4 * MAX_DATA_ROWS  # rows looked at, empty ones included, before giving up
# openpyxl walks a whole sheet when it opens the workbook if the file carries no `<dimension>` (a
# generated one need not), so a huge tab costs seconds before the first row is read. The unpacked
# size of every sheet is in the zip directory: refuse an oversize one without reading it.
MAX_SHEET_XML_BYTES = 16 * 1024 * 1024
_SHEET_XML = re.compile(r"^xl/worksheets/[^/]+\.xml$")
_PATH = re.compile(r"^(?P<tab>[^!]+)!row(?P<row>\d+):col(?P<col>\w+)$")
_TAB_TYPES = {tab: master_type for master_type, tab in TABS.items()}


def import_workbook(
    session: Session,
    user: CurrentUser,
    settings: Settings,
    storage: StorageBackend,
    filename: str,
    data: bytes,
    mode: ImportMode,
    project_id: uuid.UUID | None,
) -> ImportReport:
    if project_id is not None:
        _active_project(session, user, project_id)
    scope = StorageScope(user.context().tenant_id, project_id or TENANT_SCOPE_PROJECT)
    sha256, parsed = read_workbook(session, user, settings, storage, scope, filename, data)
    _require_frozen_filetype(parsed.rows, parsed.errors, current_payloads(session))

    errors = [_row_error(e) for e in parsed.errors]
    write = mode == ImportMode.UPSERT and not errors
    # _apply flushes version bumps, so a concurrent edit can surface there, not only at commit.
    try:
        tabs = _apply(session, user, parsed.rows, project_id, write)
        by_name = {t.name: t for t in tabs}
        for error in errors:
            if error.tab not in by_name:
                by_name[error.tab] = ImportTabReport(name=error.tab)
                tabs.append(by_name[error.tab])
            by_name[error.tab].errors.append(error)
        report = ImportReport(
            mode=mode,
            applied=write,
            sha256=sha256,
            tabs=tabs,
            stable_ids=[r.identity.stable_id for r in parsed.rows],
            statuses=dict(Counter(r.identity.status for r in parsed.rows)),
        )
        _audit_import(session, user, report, project_id, filename)
        session.commit()
    except (IntegrityError, StaleRowVersion):  # another import or edit won the race
        session.rollback()
        raise version_conflict() from None
    return report


# --- file checks ----------------------------------------------------------------------------


def read_workbook(
    session: Session,
    user: CurrentUser,
    settings: Settings,
    storage: StorageBackend,
    scope: StorageScope,
    filename: str,
    data: bytes,
) -> tuple[str, WorkbookReport]:
    """Size, scan, container and structure checks shared by the masters and Filetype imports:
    (sha256 of the file, the parsed workbook). Raises 413/415/422/503 like the import; an
    infected workbook is quarantined under `scope`."""
    if len(data) > MAX_WORKBOOK_BYTES:
        raise ApiError(413, ErrorCode.FILE_TOO_LARGE, "The workbook is larger than 15 MB")
    sha256 = f"sha256:{hashlib.sha256(data).hexdigest()}"
    _scan(session, user, settings, storage, scope, sha256, data)
    parsed = parse_workbook(_read(filename, data))
    if parsed.code == ErrorCode.STRUCTURE_MISMATCH:
        raise _structure_mismatch(parsed.errors)
    return sha256, parsed


def _structure_mismatch(errors: list[FieldError]) -> ApiError:
    return ApiError(
        422,
        ErrorCode.STRUCTURE_MISMATCH,
        "The workbook does not have the BAM 0.2 structure",
        "Use the published template; columns and tabs are never mapped automatically.",
        errors=errors,
    )


def _scan(
    session: Session,
    user: CurrentUser,
    settings: Settings,
    storage: StorageBackend,
    scope: StorageScope,
    sha256: str,
    data: bytes,
) -> None:
    clamd = ClamdClient(settings.clamav_host, settings.clamav_port, settings.clamav_timeout)
    try:
        with CLAMD.slot():
            verdict = clamd.scan(io.BytesIO(data))
    except (ClamdError, BulkheadFullError):
        raise scan_unavailable() from None
    if verdict.infected:
        stored = storage.put(data, scope, Kind.QUARANTINE, XLSX, max_bytes=MAX_WORKBOOK_BYTES)
        record(
            session,
            tenant_id=user.tenant_id,
            actor_id=str(user.user_id),
            action="MASTERS_IMPORT_REJECTED",
            entity_type="masters_import",
            entity_id=sha256,
            after={
                "sha256": sha256,
                "signature": verdict.signature,
                "reason": "MALWARE",
                "quarantine_key": stored.storage_key,
            },
            correlation_id=_correlation(),
        )
        session.commit()
        raise ApiError(422, ErrorCode.MALWARE, "The workbook contains malware and was quarantined")


def _unsupported(errors: list[FieldError]) -> ApiError:
    return ApiError(
        415, ErrorCode.UNSUPPORTED_TYPE, "Only .xlsx workbooks without macros", errors=errors
    )


def _read(filename: str, data: bytes) -> dict[str, Grid]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise _unsupported([FieldError(path="file", message="not an .xlsx workbook")]) from None
    with archive:
        infos = archive.infolist()
        if errors := check_workbook_container(filename, len(data), (i.filename for i in infos)):
            raise _unsupported(errors)
        if bomb := check_archive((i.filename, i.file_size, i.compress_size) for i in infos):
            raise ApiError(
                422, ErrorCode.ARCHIVE_LIMIT, "The workbook expands too far", errors=[bomb]
            )
        for info in infos:
            if _SHEET_XML.match(info.filename) and info.file_size > MAX_SHEET_XML_BYTES:
                raise _too_many_rows(info.filename)
        try:
            content_types = archive.read("[Content_Types].xml").decode("utf-8", "replace")
        except (KeyError, zipfile.BadZipFile, ValueError):
            content_types = ""
        if error := check_content_types(content_types):
            raise _unsupported([error])
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:  # openpyxl raises many types on a broken package
        raise _unsupported(
            [FieldError(path="file", message="the workbook can't be read")]
        ) from None
    try:
        return _stream_sheets(book)
    finally:
        book.close()


def _too_many_rows(tab: str) -> ApiError:
    return ApiError(
        422,
        ErrorCode.VALIDATION_ERROR,
        "A tab is too large",
        f"{tab} is larger than a masters tab can be ({MAX_DATA_ROWS} data rows).",
        errors=[FieldError(path=f"{tab}!rows", message=f"more than {MAX_DATA_ROWS} data rows")],
    )


def _stream_sheets(book: Any) -> dict[str, Grid]:
    """The tabs, checked before any row is read, then each tab's rows up to `MAX_DATA_ROWS`."""
    names = tuple(book.sheetnames)
    if names not in (TAB_ORDER, TAB_ORDER_WITH_TOOLING):  # the same tab error the full check gives
        raise _structure_mismatch(validate_structure({name: [] for name in names}))
    grids: dict[str, Grid] = {}
    for sheet in book.worksheets:
        rows: list[list[Any]] = []
        filled = 0  # data rows (below the header) that hold a value
        for number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
            row = list(values)  # row positions are kept: error paths cite the sheet row
            rows.append(row)
            if number > HEADER_ROWS and any(cell not in (None, "") for cell in row):
                filled += 1
            if filled > MAX_DATA_ROWS or number > _MAX_SCAN_ROWS:
                raise _too_many_rows(sheet.title)
        grids[sheet.title] = rows
    return grids


def _require_frozen_filetype(
    rows: list[MasterRow], errors: list[FieldError], frozen: list[FiletypePayload]
) -> None:
    """A tenant import must carry the platform's current Filetype rows (E3.13: the shipped rows or
    a super-admin's newer version); only POST /admin/filetypes/import changes them."""
    if any(e.path.startswith(f"{TABS[MasterType.FILETYPE]}!") for e in errors):
        return  # its row errors are reported like any other tab's
    got = [r for r in rows if r.master_type == MasterType.FILETYPE]
    changed = [
        FieldError(
            path=f"Filetype!row{row.sheet_row}:colA",
            message=f"{row.row_label.strip()} differs from the frozen row",
        )
        for row, want in zip(got, frozen, strict=False)
        if row.payload != want
    ]
    if changed or len(got) != len(frozen):
        raise ApiError(
            403,
            ErrorCode.FILETYPE_FROZEN,
            "The Filetype master is frozen",
            "Only a super-admin can change the 43 Filetype rows.",
            errors=changed,
        )


def _row_error(error: FieldError) -> ImportRowError:
    match = _PATH.match(error.path)
    if match is None:
        return ImportRowError(tab=error.path.split("!")[0], message=error.message)
    tab, col = match["tab"], match["col"]
    master_type = _TAB_TYPES.get(tab)
    column: str | None = "Name" if col == "A" else None
    if master_type is not None and col.isdigit():
        index = int(col) - COLUMN_NUMBER_START[master_type]
        names = COLUMNS[master_type]
        column = names[index][0] if 0 <= index < len(names) else None
    return ImportRowError(tab=tab, row=int(match["row"]), column=column, message=error.message)


# --- upsert ---------------------------------------------------------------------------------


def _apply(
    session: Session,
    user: CurrentUser,
    rows: list[MasterRow],
    project_id: uuid.UUID | None,
    write: bool,
) -> list[ImportTabReport]:
    """Count created / updated / unchanged / missing per tab; with `write`, also write them."""
    repo = _repo(session, user)
    tabs: list[ImportTabReport] = []
    for master_type, tab in TABS.items():
        report = ImportTabReport(name=tab)
        tabs.append(report)
        existing = {
            e.stable_id: e
            for e in repo.of_type(master_type.value, project_id)
            if e.project_id == project_id
        }
        current = repo.current_versions(list(existing.values()))
        seen: set[str] = set()
        for row in (r for r in rows if r.master_type == master_type):
            seen.add(row.identity.stable_id)
            payload = row.payload.model_dump(mode="json", by_alias=True)
            entry = existing.get(row.identity.stable_id)
            if entry is None:
                report.created += 1
                if write:
                    _create(session, user, repo, row, payload, project_id)
            elif entry.id in current and current[entry.id].payload == payload:
                report.unchanged += 1
            else:
                report.updated += 1
                if write:
                    _new_version(session, user, repo, entry, row, payload)
        report.missing = len(existing.keys() - seen)
    return tabs


def _create(
    session: Session,
    user: CurrentUser,
    repo: MasterEntryRepository,
    row: MasterRow,
    payload: dict[str, Any],
    project_id: uuid.UUID | None,
) -> None:
    entry = repo.add(
        MasterEntry(
            project_id=project_id,
            master_type=row.master_type.value,
            stable_id=row.identity.stable_id,
            row_label=row.row_label.strip(),
            status=row.identity.status.value,
            source_kind=row.identity.source_kind.value,
            current_version=1,
            created_by=user.keycloak_sub,
        )
    )
    version = repo.add_version(
        MasterEntryVersion(
            entry_id=entry.id, version=1, payload=payload, created_by=user.keycloak_sub
        )
    )
    _audit(session, user, "MASTER_ENTRY_CREATED", entry, version)


def _new_version(
    session: Session,
    user: CurrentUser,
    repo: MasterEntryRepository,
    entry: MasterEntry,
    row: MasterRow,
    payload: dict[str, Any],
) -> None:
    number = entry.current_version + 1
    version = repo.add_version(
        MasterEntryVersion(
            entry_id=entry.id, version=number, payload=payload, created_by=user.keycloak_sub
        )
    )
    entry.current_version = number
    entry.status = row.identity.status.value
    repo.update(entry, entry.row_version)
    _audit(session, user, "MASTER_VERSION_CREATED", entry, version)


def _audit_import(
    session: Session,
    user: CurrentUser,
    report: ImportReport,
    project_id: uuid.UUID | None,
    filename: str,
) -> None:
    record(
        session,
        tenant_id=user.tenant_id,
        actor_id=str(user.user_id),
        action="MASTERS_IMPORTED",
        entity_type="masters_import",
        entity_id=report.sha256,
        after={
            "sha256": report.sha256,
            "filename": filename[:255],
            "mode": report.mode.value,
            "applied": report.applied,
            "project_id": str(project_id) if project_id else None,
            "counts": {
                t.name: {
                    "created": t.created,
                    "updated": t.updated,
                    "unchanged": t.unchanged,
                    "missing": t.missing,
                    "errors": len(t.errors),
                }
                for t in report.tabs
            },
        },
        correlation_id=_correlation(),
    )
