"""The choose-a-repository question (#756). W is `/projects/{project_id}/workflows/{workflow_id}`.

- `GET W/repo-questions` -> `RepoQuestions`: the builds waiting for the FDE's repository answer
  (short list, scores, reasons) and the repository decisions already recorded.
- `POST W/sub-skills/{sub_id}/repo-answer` -> 202 `RepoAnswered`: tell the waiting build which
  repository to use, or that none fits. Needs an Idempotency-Key; 409 NOT_IN_REVIEW when the build
  is not waiting for it; 422 for a choice that is not on the short list.

A project the FDE is not assigned to, another tenant's, or an unknown Workflow or Sub-Skill is
a 404.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sbam_contracts.repo_choice import RepoAnswered, RepoAnswerRequest, RepoQuestions
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import validate_idempotency_key
from sbam_api.projects.guard import active_project
from sbam_api.repo_choice import service
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["repo-choice"])

Project = Annotated[UUID, Depends(active_project)]  # assigned FDE; archived: writes 409
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
W = "/projects/{project_id}/workflows/{workflow_id}"


@router.get(f"{W}/repo-questions")
def repo_questions(
    project_id: Project, workflow_id: UUID, user: User, sessions: Sessions
) -> RepoQuestions:
    with sessions() as session:
        return service.questions(session, user, project_id, workflow_id)


@router.post(f"{W}/sub-skills/{{sub_id}}/repo-answer", status_code=202)
async def repo_answer(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    body: RepoAnswerRequest,
    request: Request,
    user: User,
    sessions: Sessions,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RepoAnswered:
    key = validate_idempotency_key(idempotency_key)
    client = await temporal_client(request)
    with sessions() as session:
        return await service.answer_question(
            client, session, user, project_id, workflow_id, sub_id, key, body
        )
