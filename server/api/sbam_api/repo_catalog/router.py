"""`GET /projects/{project_id}/repo-catalog`: the repository candidates of a project, read-only.

The build reads this to offer repositories (ADR 0028). It is auto-discovered; it changes nothing
in the masters and nobody's right to edit them.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sbam_contracts.repo_catalog import RepoCatalog
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.projects.guard import active_project
from sbam_api.repo_catalog import service
from sbam_api.temporal.deps import session_factory

router = APIRouter(tags=["repo-catalog"])

Project = Annotated[
    UUID, Depends(active_project)
]  # assigned FDE; another tenant or unassigned: 404
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]


@router.get("/projects/{project_id}/repo-catalog")
def repo_catalog(project_id: Project, user: User, sessions: Sessions) -> RepoCatalog:
    with sessions() as session:
        return service.build(session, user, project_id)
