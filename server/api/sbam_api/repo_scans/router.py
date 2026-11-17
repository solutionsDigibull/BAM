"""`GET /projects/{project_id}/repo-scans/{report_sha256}`: the scan report of a fetched repo.

The report is content-addressed: its address is the sha256 of its canonical JSON, inside the
project's storage scope. The project guard makes another tenant, or a project the FDE is not
assigned to, a 404; a hash that is not a report of this project (nothing stored, or another kind
of object) is also a 404.
"""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import ValidationError
from sbam_contracts.repo_scan import RepoScanReport

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import not_found
from sbam_api.projects.guard import active_project
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.router import backend

router = APIRouter(tags=["repo-scans"])

Project = Annotated[UUID, Depends(active_project)]  # assigned FDE; otherwise 404
User = Annotated[CurrentUser, Depends(fde)]
Storage = Annotated[StorageBackend, Depends(backend)]
Sha256 = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


@router.get("/projects/{project_id}/repo-scans/{report_sha256}")
def repo_scan(
    project_id: Project, report_sha256: Sha256, user: User, storage: Storage
) -> RepoScanReport:
    scope = StorageScope(user.context(project_id).tenant_id, project_id)
    key = scope.key(Kind.REPOS, report_sha256)
    try:
        if not storage.exists(key):
            raise not_found()
        raw = storage.get(key, verify=True)
        return RepoScanReport.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        raise not_found() from None  # stored, but not a report (the repository tree, for one)
