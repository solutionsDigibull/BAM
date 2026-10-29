"""Test runs endpoint (E13.6). FDE portal.

* `GET W/sub-skills/{sub_id}/test-runs`: the Sub-Skill's Promptfoo runs, newest first
  (`TestRunsResponse`); empty before any. 404 for another tenant's, an unassigned project's or
  another Workflow's Sub-Skill.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sbam_contracts.promptfoo import TestRunsResponse
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.projects.guard import active_project
from sbam_api.temporal.deps import session_factory
from sbam_api.testruns import service

router = APIRouter(tags=["testruns"])

W = "/projects/{project_id}/workflows/{workflow_id}"
Project = Annotated[UUID, Depends(active_project)]
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]


@router.get(W + "/sub-skills/{sub_id}/test-runs")
def get_test_runs(
    project_id: Project, workflow_id: UUID, sub_id: UUID, user: User, sessions: Sessions
) -> TestRunsResponse:
    with sessions() as session:
        return service.list_runs(session, user.context(project_id), workflow_id, sub_id)
