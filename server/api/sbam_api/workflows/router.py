"""Workflow endpoints (TD §8.4, E8.1):

* `GET P/workflows`: the project's Workflows, newest MAJOR first.
* `PATCH P/workflows/{workflow_id}`: rename only (`WorkflowRename`; any other field is 422).
  `If-Match` required (428 missing, 412 stale); CERTIFIED / FROZEN → 423.

There is no route to create a Workflow: SBAM creates it when a SOW version is approved, so a POST
answers 405.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from sbam_contracts.api import Page
from sbam_contracts.projects import WorkflowRename, WorkflowSummary
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.conventions import etag
from sbam_api.temporal.deps import session_factory
from sbam_api.workflows import service

router = APIRouter(tags=["workflows"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]


@router.get("/projects/{project_id}/workflows")
def workflows(project_id: Project, user: User, sessions: Sessions) -> Page[WorkflowSummary]:
    with sessions() as session:
        rows = service.list_workflows(session, user, project_id)
        return Page[WorkflowSummary](items=[service.summary(r) for r in rows])


@router.patch("/projects/{project_id}/workflows/{workflow_id}")
def rename(
    project_id: Project,
    workflow_id: UUID,
    body: WorkflowRename,
    user: User,
    sessions: Sessions,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> WorkflowSummary:
    with sessions() as session:
        row = service.rename(session, user, project_id, workflow_id, body, if_match)
        response.headers["ETag"] = etag(row.row_version)
        return service.summary(row)
