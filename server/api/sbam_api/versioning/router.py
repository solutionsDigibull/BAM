"""Workflow version routes (#132). W is `/projects/{project_id}/workflows/{workflow_id}`.

* `GET W/versions`: the frozen versions MAJOR.0.PATCH, newest first.
* `POST W/sub-skills/{sub_skill_id}/new-version` with `Idempotency-Key`: 202, opens the next PATCH;
  409 `NOT_FROZEN` unless the Sub-Skill (and its Workflow) is FROZEN.

Only the assigned FDE (`active_project`): another tenant, an unassigned FDE and a missing id
are 404.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sbam_contracts.workflow_versions import (
    NewVersionRequest,
    NewVersionStarted,
    WorkflowVersionList,
)
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import validate_idempotency_key
from sbam_api.projects.guard import active_project
from sbam_api.temporal.deps import session_factory
from sbam_api.versioning import service

router = APIRouter(tags=["workflow-versions"])

Project = Annotated[UUID, Depends(active_project)]
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
W = "/projects/{project_id}/workflows/{workflow_id}"


@router.get(W + "/versions")
def versions(
    project_id: Project, workflow_id: UUID, user: User, sessions: Sessions
) -> WorkflowVersionList:
    with sessions() as session:
        return service.list_versions(session, user, project_id, workflow_id)


@router.post(W + "/sub-skills/{sub_skill_id}/new-version", status_code=202)
def new_version(
    project_id: Project,
    workflow_id: UUID,
    sub_skill_id: UUID,
    body: NewVersionRequest,
    user: User,
    sessions: Sessions,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> NewVersionStarted:
    key = validate_idempotency_key(idempotency_key)
    with sessions() as session:
        return service.start_patch(session, user, project_id, workflow_id, sub_skill_id, body, key)
