"""Platform queries on `filetype_row_versions` (E3.13). Platform data like `tenants`: no tenant
scope, reached only from the super-admin import and the platform view."""

from sbam_contracts.masters import FiletypePayload
from sbam_contracts.masters_workbook import frozen_filetype
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbam_api.filetypes.models import FiletypeRowVersion


def label(index: int) -> str:
    """Row label of the index-th Filetype row (0-based), as in the workbook."""
    return f"Filetype #{index + 1}"


def latest_versions(session: Session) -> dict[str, FiletypeRowVersion]:
    newest = (
        select(FiletypeRowVersion.row_label, func.max(FiletypeRowVersion.version).label("v"))
        .group_by(FiletypeRowVersion.row_label)
        .subquery()
    )
    rows = session.scalars(
        select(FiletypeRowVersion).join(
            newest,
            (FiletypeRowVersion.row_label == newest.c.row_label)
            & (FiletypeRowVersion.version == newest.c.v),
        )
    )
    return {row.row_label: row for row in rows}


def current_payloads(session: Session) -> list[FiletypePayload]:
    """The 43 rows as they stand: the shipped row, or its newest super-admin version."""
    latest = latest_versions(session)
    return [
        FiletypePayload.model_validate(latest[label(i)].payload) if label(i) in latest else row
        for i, row in enumerate(frozen_filetype())
    ]
