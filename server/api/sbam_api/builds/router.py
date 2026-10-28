"""Sub-Skill build endpoints (TD §8): POST W/sub-skills/{sub_id}/retry (E10.2).

202 `RetryAccepted` once the build was told to retry; 409 NOT_IN_REVIEW when the Sub-Skill is not
in NEEDS_FDE_REVIEW or a failed state; 404 for a project the FDE isn't assigned to or an unknown
Sub-Skill; 400 without a valid Idempotency-Key; 503 when Temporal is down. W is
`/projects/{project_id}/workflows/{workflow_id}`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from sbam_contracts.build import RetryAccepted, RetryRequest, SubSkillRetry
from sbam_contracts.code_view import CodeAttemptList, CodeDiff, CodeFileView
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.builds import code_view
from sbam_api.builds.service import retry_build
from sbam_api.core.auth import CurrentUser, fde, project_access
from sbam_api.core.conventions import validate_idempotency_key
from sbam_api.storage.router import backend
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["builds"])

Project = Annotated[UUID, Depends(project_access)]  # fde policy + assignment check
User = Annotated[CurrentUser, Depends(fde)]
Sessions = Annotated[sessionmaker[Session], Depends(session_factory)]
BuildNo = Annotated[int | None, Query(ge=1)]
Cycle = Annotated[int | None, Query(ge=0)]
CodePath = Annotated[str, Query(min_length=1, max_length=256)]
Attempt = Annotated[int, Path(ge=0, le=2)]
CODE = "/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/code"


@router.get(f"{CODE}/attempts")
def code_attempts(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    user: User,
    sessions: Sessions,
    build_no: BuildNo = None,
    cycle: Cycle = None,
) -> CodeAttemptList:
    with sessions() as session:
        return code_view.attempts(session, user, project_id, workflow_id, sub_id, build_no, cycle)


@router.get(f"{CODE}/attempts/{{n}}/files")
def code_file(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    n: Attempt,
    path: CodePath,
    request: Request,
    user: User,
    sessions: Sessions,
    build_no: BuildNo = None,
    cycle: Cycle = None,
) -> CodeFileView:
    with sessions() as session:
        return code_view.file_view(
            session,
            backend(request),
            user,
            project_id,
            workflow_id,
            sub_id,
            n,
            path,
            build_no,
            cycle,
        )


@router.get(f"{CODE}/attempts/{{n}}/diff/{{m}}")
def code_diff(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    n: Attempt,
    m: Attempt,
    path: CodePath,
    request: Request,
    user: User,
    sessions: Sessions,
    build_no: BuildNo = None,
    cycle: Cycle = None,
) -> CodeDiff:
    with sessions() as session:
        return code_view.diff(
            session,
            backend(request),
            user,
            project_id,
            workflow_id,
            sub_id,
            n,
            m,
            path,
            build_no,
            cycle,
        )


@router.post(
    "/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/retry",
    status_code=202,
    response_model=RetryAccepted,
)
async def retry(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    body: RetryRequest,
    request: Request,
    user: User,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RetryAccepted:
    key = validate_idempotency_key(idempotency_key)
    client = await temporal_client(request)
    return await retry_build(
        client,
        tenant_id=user.context(project_id).tenant_id,
        project_id=project_id,
        workflow_id=workflow_id,
        sub_skill_id=sub_id,
        retry=SubSkillRetry(reason=body.reason, idempotency_key=key, actor_id=str(user.user_id)),
    )
