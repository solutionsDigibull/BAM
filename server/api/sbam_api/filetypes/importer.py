"""Super-admin Filetype import (E3.13, ADR 0020): POST /admin/filetypes/import.

The one way the 43 platform Filetype rows (O-23) change. The workbook goes through the same checks
as the masters import (size, ClamAV, container, BAM 0.2 structure: still 43 rows labelled
Filetype #1..#43, else 422 STRUCTURE_MISMATCH); only its Filetype tab is used. Each row that differs
from the current platform row gets a new version, all in one transaction, with one
FILETYPE_VERSION_IMPORTED audit row (platform-level: no tenant). Unchanged rows and a workbook
imported twice write nothing. The loader configuration (`filetypes.yaml`: mvp_enabled,
default_route, ...) is never read from or written by the workbook.
"""

import logging
from collections import Counter

from sbam_contracts.masters import FiletypePayload, MasterType
from sbam_contracts.masters_api import ImportMode, ImportReport, ImportTabReport
from sbam_contracts.masters_workbook import TABS
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.settings import Settings
from sbam_api.filetypes.config import load
from sbam_api.filetypes.models import FiletypeRowVersion
from sbam_api.filetypes.repository import current_payloads, label, latest_versions
from sbam_api.masters.importer import _row_error, read_workbook
from sbam_api.masters.service import _correlation, version_conflict

log = logging.getLogger("sbam_api.filetypes")
TAB = TABS[MasterType.FILETYPE]


def import_filetypes(
    session: Session,
    user: CurrentUser,
    settings: Settings,
    filename: str,
    data: bytes,
    mode: ImportMode,
    version_label: str | None,
) -> ImportReport:
    sha256, parsed = read_workbook(session, user, settings, filename, data)
    rows = [r for r in parsed.rows if r.master_type == MasterType.FILETYPE]
    errors = [_row_error(e) for e in parsed.errors if e.path.startswith(f"{TAB}!")]
    # by row label, not position: a row with an error is not in `rows`
    current = {label(i): (i, payload) for i, payload in enumerate(current_payloads(session))}
    changed = [
        (current[key][0], row.payload, current[key][1])
        for row in rows
        if (key := row.row_label.strip()) in current
        and isinstance(row.payload, FiletypePayload)
        and row.payload != current[key][1]
    ]
    write = mode == ImportMode.UPSERT and not errors
    tab = ImportTabReport(
        name=TAB, updated=len(changed), unchanged=len(rows) - len(changed), errors=errors
    )
    report = ImportReport(
        mode=mode,
        applied=write,
        sha256=sha256,
        tabs=[tab],
        stable_ids=[r.identity.stable_id for r in rows],
        statuses=dict(Counter(r.identity.status for r in rows)),
    )
    if write and changed:
        try:
            _write(session, user, sha256, version_label, changed)
            session.commit()
        except IntegrityError:  # another super-admin import won the race for a version number
            session.rollback()
            raise version_conflict() from None
    return report


def _write(
    session: Session,
    user: CurrentUser,
    sha256: str,
    version_label: str | None,
    changed: list[tuple[int, FiletypePayload, FiletypePayload]],
) -> None:
    latest = latest_versions(session)
    enabled = {f.row_label for f in load() if f.config.mvp_enabled}
    warnings = []
    for i, new, old in changed:
        row_label = label(i)
        previous = latest.get(row_label)
        session.add(
            FiletypeRowVersion(
                row_label=row_label,
                version=(previous.version if previous else 1) + 1,
                payload=new.model_dump(mode="json"),
                workbook_sha256=sha256,
                version_label=version_label,
                created_by=str(user.user_id),
            )
        )
        if row_label in enabled and new.file_format != old.file_format:
            # The loader config still decides how this type is parsed (filetypes.yaml, ADR 0020).
            warnings.append(f"{row_label} is enabled for loading and its file format changed")
    for warning in warnings:
        log.warning("filetype import: %s", warning)
    record(
        session,
        tenant_id=None,  # platform data: one Filetype table for every tenant
        actor_id=str(user.user_id),
        action="FILETYPE_VERSION_IMPORTED",
        entity_type="filetypes",
        entity_id=sha256,
        before={label(i): old.model_dump(mode="json") for i, _, old in changed},
        after={
            "workbook_sha256": sha256,
            "version_label": version_label,
            "changed": [label(i) for i, _, _ in changed],
            "rows": {label(i): new.model_dump(mode="json") for i, new, _ in changed},
            "warnings": warnings,
        },
        correlation_id=_correlation(),
    )
    session.flush()
