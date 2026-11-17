"""`GET /projects/{project_id}/workflows/{workflow_id}/registration`: the lookup contract (#760).

Returns the registry entry of one Workflow version, or the newest enabled one: the registry name,
the image digest and manifest hash that were certified, the tools with their schemas, and whether
a later version superseded it. Only a registered (so certified and frozen) Workflow has an entry:
nothing else is ever visible here. Read-only. Another tenant or an unassigned project is a 404
before this runs.

Registering itself is not a route: the Commit step (#766) does it through `service.register`,
only after the Workflow is frozen.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sbam_contracts.manifest import VERSION_PATTERN
from sbam_contracts.workflow_registry import RegisteredWorkflow
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.projects.guard import active_project
from sbam_api.temporal.deps import session_factory
from sbam_api.workflow_registry import service

router = APIRouter(tags=["workflow-registry"])

Project = Annotated[UUID, Depends(active_project)]  # assigned FDE; another tenant: 404
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]


@router.get("/projects/{project_id}/workflows/{workflow_id}/registration")
def workflow_registration(
    project_id: Project,
    workflow_id: UUID,
    user: User,
    sessions: Sessions,
    version: Annotated[str | None, Query(pattern=VERSION_PATTERN)] = None,
) -> RegisteredWorkflow:
    with sessions() as session:
        return service.lookup(session, user.context(project_id), workflow_id, version)
