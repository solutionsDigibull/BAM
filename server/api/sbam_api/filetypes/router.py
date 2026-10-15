"""Filetype table (E3.7): the 43 frozen Filetype rows with SBAM's loader configuration.

Read-only for every portal role (sbam-admin, super-admin, sbam-fde). The rows are platform data, the
same for every tenant; changing them is a super-admin workbook version (E3.3), never this API.
"""

from fastapi import APIRouter, Depends
from sbam_contracts.masters_api import FiletypeInfo

from sbam_api.core.auth import authenticated
from sbam_api.filetypes.config import Filetype, load

router = APIRouter(tags=["filetypes"])

# Loaded (and validated) when the module is imported, i.e. at API startup: a bad configuration
# raises ConfigError and the API doesn't start (E3.7-T06).
FILETYPES = load()


def _info(f: Filetype) -> FiletypeInfo:
    return FiletypeInfo(
        row_label=f.row_label,
        file_format=f.frozen.file_format,
        tier=f.frozen.tier,
        purpose=f.frozen.purpose,
        rag_ingestion=f.frozen.rag_ingestion,
        example=f.frozen.example,
        mvp_enabled=f.config.mvp_enabled,
        loader=f.config.loader,
        extensions=list(f.config.extensions),
        intent_class=f.config.intent_class,
        default_route=f.config.default_route,
    )


@router.get("/admin/filetypes", dependencies=[Depends(authenticated)])
def list_filetypes() -> list[FiletypeInfo]:
    """All 43, in row-label order (Filetype #1 .. #43)."""
    return [_info(f) for f in FILETYPES]
