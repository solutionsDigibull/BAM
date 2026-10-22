"""Masters engine API (E3.2): entries, versions and snapshots under `/admin/masters`.

Snapshots are made by the modules that use the masters (SOW generation, knowledge builds) through
`service.create_snapshot`; the API only reads them.
"""

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Header, Request, Response, UploadFile
from sbam_contracts.api import Page
from sbam_contracts.masters import MasterStatus, MasterType, SourceKind
from sbam_contracts.masters_api import (
    ImportMode,
    ImportReport,
    MasterEntry,
    MasterEntryCreate,
    MastersSnapshot,
    MasterVersionCreate,
)
from sbam_contracts.masters_workbook import MAX_WORKBOOK_BYTES
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser, admin
from sbam_api.core.conventions import etag
from sbam_api.db.session import app_sessions
from sbam_api.masters import exporter, importer, models, service

router = APIRouter(tags=["masters"])

Admin = Annotated[CurrentUser, Depends(admin)]


def db(request: Request) -> Iterator[Session]:
    with app_sessions(request.app.state)() as session:
        yield session


Db = Annotated[Session, Depends(db)]


def _out(
    entry: models.MasterEntry,
    version: models.MasterEntryVersion,
    response: Response | None = None,
) -> MasterEntry:
    if response is not None:
        response.headers["ETag"] = etag(entry.row_version)
    return MasterEntry(
        id=entry.id,
        tenant_id=entry.tenant_id,
        project_id=entry.project_id,
        master_type=MasterType(entry.master_type),
        stable_id=entry.stable_id,
        row_label=entry.row_label,
        status=MasterStatus(entry.status),
        source_kind=SourceKind(entry.source_kind),
        version=version.version,
        payload=version.payload,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _snapshot(row: models.MasterSnapshot) -> MastersSnapshot:
    return MastersSnapshot.model_validate(
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "project_id": row.project_id,
            "entries": row.entries,
            "sha256": row.sha256,
            "created_at": row.created_at,
        }
    )


# Declared before `/{master_type}/…` so "snapshots", "import", "export" and "template" are never
# read as a master type.
@router.get("/admin/masters/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: uuid.UUID, user: Admin, session: Db) -> MastersSnapshot:
    return _snapshot(service.get_snapshot(session, user, snapshot_id))


@router.post("/admin/masters/import")
def import_workbook(
    request: Request,
    file: UploadFile,
    user: Admin,
    session: Db,
    mode: Annotated[ImportMode, Form()] = ImportMode.VALIDATE_ONLY,
    project_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ImportReport:
    """Validate or upsert the BAM 0.2 masters workbook (E3.3); see `importer`."""
    data = file.file.read(MAX_WORKBOOK_BYTES + 1)  # one byte over is enough to answer 413
    settings = request.app.state.settings
    return importer.import_workbook(
        session, user, settings, file.filename or "", data, mode, project_id
    )


@router.get(
    "/admin/masters/export",
    response_class=Response,
    responses={200: {"content": {exporter.XLSX: {}}, "description": "The masters workbook"}},
)
def export_workbook(user: Admin, session: Db, project_id: uuid.UUID | None = None) -> Response:
    """The caller's masters in the BAM 0.2 layout (E3.4); see `exporter`."""
    name, data = exporter.export(session, user, project_id)
    return _xlsx(name, data)


@router.get(
    "/admin/masters/template",
    response_class=Response,
    responses={200: {"content": {exporter.XLSX: {}}, "description": "The empty template"}},
)
def workbook_template(user: Admin) -> Response:
    """The empty BAM 0.2 workbook: README, header rows and the frozen Filetype rows (E3.4)."""
    return _xlsx("BAM_masters_template.xlsx", exporter.template())


def _xlsx(name: str, data: bytes) -> Response:
    headers = {
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store",
    }
    return Response(content=data, media_type=exporter.XLSX, headers=headers)


@router.post("/admin/masters/{master_type}", status_code=201)
def create_entry(
    master_type: MasterType,
    body: MasterEntryCreate,
    user: Admin,
    session: Db,
    response: Response,
) -> MasterEntry:
    return _out(*service.create_entry(session, user, master_type, body), response)


@router.get("/admin/masters/{master_type}")
def list_entries(
    master_type: MasterType,
    user: Admin,
    session: Db,
    project_id: uuid.UUID | None = None,
) -> Page[MasterEntry]:
    rows = service.list_entries(session, user, master_type, project_id)
    return Page[MasterEntry](items=[_out(e, v) for e, v in rows])


@router.get("/admin/masters/{master_type}/{entry_id}")
def get_entry(
    master_type: MasterType, entry_id: uuid.UUID, user: Admin, session: Db, response: Response
) -> MasterEntry:
    return _out(*service.get_entry(session, user, master_type, entry_id), response)


@router.post("/admin/masters/{master_type}/{entry_id}/versions", status_code=201)
def create_version(
    master_type: MasterType,
    entry_id: uuid.UUID,
    body: MasterVersionCreate,
    user: Admin,
    session: Db,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> MasterEntry:
    entry, version = service.new_version(session, user, master_type, entry_id, body, if_match)
    return _out(entry, version, response)
