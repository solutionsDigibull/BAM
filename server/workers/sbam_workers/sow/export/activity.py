"""`export_sow` (E6.4): render one SOW version as md, docx or pdf, on `sbam-build`.

`export_version` takes its dependencies as arguments, so tests run it with SQLite and a temp-dir
storage. For `(sow_version_id, format)`:

  1. the version must exist in the project and have content (a GENERATION_FAILED version is
     `ExportNotAllowed`), and its content must still hash to its `sha256` (`HASH_MISMATCH`);
  2. an export of the same version, status, format and template is returned as it is: the same
     file is never rendered twice;
  3. otherwise the Markdown source is built from `structured` (never from TipTap), rendered,
     stored under `exports/` and recorded in `sow_exports`.
`SowExport` (`workflow.py`) only runs this activity, so the API can wait for it and redirect.
Logs carry ids and sizes only.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from sbam_api.db.repository import TenantContext
from sbam_api.knowledge.graph import _titles
from sbam_api.projects.repository import ProjectRepository
from sbam_api.sow.models import SowExport, SowVersion
from sbam_api.sow.repository import SowExportRepository, SowVersionRepository
from sbam_api.sow.service import canonical_sha256
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.factory import make_backend
from sbam_contracts.runs import TaskQueue
from sbam_contracts.sow import FdeCitation, SowDocument
from sbam_contracts.sow_api import (
    EXPORT_TEMPLATE,
    ExportFormat,
    SowExportInput,
    SowExportResult,
)
from sbam_contracts.state_machines import SowVersionState
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings
from sbam_workers.sow.export.markdown import ExportMeta, build_markdown
from sbam_workers.sow.export.render import CONTENT_TYPES, render
from sbam_workers.sow.generate import _citations

log = logging.getLogger("sbam_workers.sow.export")

NOT_ALLOWED: Final = "ExportNotAllowed"


@dataclass(frozen=True)
class ExportDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend


def _result(row: SowExport) -> SowExportResult:
    return SowExportResult(storage_key=row.storage_key, sha256=row.sha256, size=row.size)


def _source(deps: ExportDeps, inp: SowExportInput) -> tuple[SowVersion, str, dict[uuid.UUID, str]]:
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        row: SowVersion | None = SowVersionRepository(session, ctx).get(inp.sow_version_id)
        if row is None:
            raise ApplicationError("no such SOW version", type=NOT_ALLOWED, non_retryable=True)
        if row.state == SowVersionState.GENERATION_FAILED.value or row.structured is None:
            raise ApplicationError(
                "a failed generation has nothing to export", type=NOT_ALLOWED, non_retryable=True
            )
        if canonical_sha256(row.structured) != row.sha256:
            raise ApplicationError(
                "content does not match its sha256", type="HASH_MISMATCH", non_retryable=True
            )
        project = ProjectRepository(session, TenantContext(inp.tenant_id)).get(inp.project_id)
        doc = SowDocument.model_validate(row.structured)
        docs = {c.document_version_id for _, c in _citations(doc) if not isinstance(c, FdeCitation)}
        titles = _titles(session, ctx, docs)
        session.expunge(row)
        return row, project.name if project is not None else "", titles


def _cached(deps: ExportDeps, inp: SowExportInput, status: str) -> SowExport | None:
    with deps.sessions() as session:
        repo = SowExportRepository(session, TenantContext(inp.tenant_id, inp.project_id))
        return repo.find(inp.sow_version_id, status, inp.format.value, EXPORT_TEMPLATE)


def export_version(deps: ExportDeps, inp: SowExportInput) -> SowExportResult:
    row, project, titles = _source(deps, inp)
    hit = _cached(deps, inp, row.state)
    if hit is not None:
        return _result(hit)
    meta = ExportMeta(project, row.version, row.state, row.sha256 or "", row.created_at)
    markdown = build_markdown(row.structured or {}, meta, titles)
    superseded = row.state == SowVersionState.SUPERSEDED.value
    data = render(markdown, inp.format, superseded=superseded)
    where = StorageScope(inp.tenant_id, inp.project_id)
    stored = deps.storage.put(data, where, Kind.EXPORTS, CONTENT_TYPES[inp.format])
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    with deps.sessions() as session:
        repo = SowExportRepository(session, ctx)
        try:
            saved = repo.add(
                SowExport(
                    sow_version_id=row.id,
                    sow_sha256=row.sha256 or "",
                    status=row.state,
                    format=inp.format.value,
                    template_version=EXPORT_TEMPLATE,
                    storage_key=stored.storage_key,
                    sha256=stored.sha256,
                    size=len(data),
                )
            )
            session.commit()
            result = _result(saved)
        except IntegrityError:  # another request rendered it first: same content, theirs wins
            session.rollback()
            other = repo.find(row.id, row.state, inp.format.value, EXPORT_TEMPLATE)
            if other is None:
                raise
            result = _result(other)
    log.info(
        "SOW exported",
        extra={
            "project_id": str(inp.project_id),
            "sow_version_id": str(row.id),
            "format": inp.format.value,
            "bytes": len(data),
        },
    )
    return result


@lru_cache(maxsize=1)
def _deps() -> ExportDeps:
    return ExportDeps(mirror.sessions(), make_backend(WorkerSettings()))  # type: ignore[call-arg]


@activity_for(TaskQueue.BUILD)
@activity.defn(name="export_sow")
async def export_sow(inp: SowExportInput) -> SowExportResult:
    return await asyncio.to_thread(export_version, _deps(), inp)


__all__ = ["ExportFormat", "export_sow", "export_version"]
