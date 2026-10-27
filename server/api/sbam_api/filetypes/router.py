"""Filetype table (E3.7): the 43 frozen Filetype rows with SBAM's loader configuration.

Read-only for every portal role (sbam-admin, super-admin, sbam-fde). The rows are platform data, the
same for every tenant; only a super-admin changes them, through a new workbook version
(POST /admin/filetypes/import, E3.13, ADR 0020). The loader configuration is never changed here.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from sbam_contracts.masters import FiletypePayload
from sbam_contracts.masters_api import FiletypeInfo, ImportMode, ImportReport
from sbam_contracts.masters_workbook import MAX_WORKBOOK_BYTES
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser, authenticated, super_admin
from sbam_api.db.session import app_sessions
from sbam_api.filetypes.config import Filetype, load
from sbam_api.filetypes.repository import current_payloads

router = APIRouter(tags=["filetypes"])

# Loaded (and validated) when the module is imported, i.e. at API startup: a bad configuration
# raises ConfigError and the API doesn't start (E3.7-T06).
FILETYPES = load()

SuperAdmin = Annotated[CurrentUser, Depends(super_admin)]


def db(request: Request) -> Iterator[Session]:
    with app_sessions(request.app.state)() as session:
        yield session


Db = Annotated[Session, Depends(db)]


def _info(f: Filetype, row: FiletypePayload) -> FiletypeInfo:
    return FiletypeInfo(
        row_label=f.row_label,
        file_format=row.file_format,
        tier=row.tier,
        purpose=row.purpose,
        rag_ingestion=row.rag_ingestion,
        example=row.example,
        mvp_enabled=f.config.mvp_enabled,
        loader=f.config.loader,
        extensions=list(f.config.extensions),
        intent_class=f.config.intent_class,
        default_route=f.config.default_route,
    )


@router.get("/admin/filetypes", dependencies=[Depends(authenticated)])
def list_filetypes(session: Db) -> list[FiletypeInfo]:
    """All 43, in row-label order (Filetype #1 .. #43), as they stand: a super-admin's newest
    version of a row, else the shipped row."""
    return [_info(f, row) for f, row in zip(FILETYPES, current_payloads(session), strict=True)]


@router.post("/admin/filetypes/import")
def import_filetypes(
    request: Request,
    file: UploadFile,
    user: SuperAdmin,
    session: Db,
    mode: Annotated[ImportMode, Form()] = ImportMode.VALIDATE_ONLY,
    version_label: Annotated[str | None, Form(max_length=100)] = None,
) -> ImportReport:
    """Validate or apply a new version of the Filetype tab (super-admin only); see `importer`."""
    # imported here: `importer` reuses the masters import, and masters imports ingest -> filetypes
    from sbam_api.filetypes import importer

    data = file.file.read(MAX_WORKBOOK_BYTES + 1)  # one byte over is enough to answer 413
    return importer.import_filetypes(
        session, user, request.app.state.settings, file.filename or "", data, mode, version_label
    )
