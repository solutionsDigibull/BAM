"""Sub-Skill build endpoints (TD §8): POST W/sub-skills/{sub_id}/retry (E10.2).

202 `RetryAccepted` once the build was told to retry; 409 NOT_IN_REVIEW when the Sub-Skill is not
in NEEDS_FDE_REVIEW or a failed state; 404 for a project the FDE isn't assigned to or an unknown
Sub-Skill; 400 without a valid Idempotency-Key; 503 when Temporal is down. W is
`/projects/{project_id}/workflows/{workflow_id}`.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from sbam_contracts.build import RetryAccepted, RetryRequest, SubSkillRetry
from sbam_contracts.code_view import CodeAttemptList, CodeDiff, CodeFileView
from sbam_contracts.held_patch import HeldPatchResponse, PatchDecided, PatchDecision
from sqlalchemy.orm import Session, sessionmaker

from sbam_api.builds import code_view, patch_decision
from sbam_api.builds.service import ensure_waiting, retry_build
from sbam_api.core.auth import CurrentUser, fde
from sbam_api.core.conventions import validate_idempotency_key
from sbam_api.projects.guard import active_project
from sbam_api.storage.router import backend
from sbam_api.temporal.deps import session_factory, temporal_client

router = APIRouter(tags=["builds"])

Project = Annotated[UUID, Depends(active_project)]  # assigned FDE; archived: writes 409
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


HELD = "/projects/{project_id}/workflows/{workflow_id}/sub-skills/{sub_id}/held-patch"
IfMatch = Annotated[str | None, Header()]
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.get(HELD)
def held_patch(
    project_id: Project, workflow_id: UUID, sub_id: UUID, user: User, sessions: Sessions
) -> HeldPatchResponse:
    with sessions() as session:
        held = patch_decision.get_held(session, user.context(project_id), workflow_id, sub_id)
        return HeldPatchResponse(held=held)


async def _decide(
    request: Request,
    sessions: sessionmaker[Session],
    user: CurrentUser,
    project_id: UUID,
    workflow_id: UUID,
    sub_id: UUID,
    key: str,
    body: PatchDecision,
    decision: Literal["ACCEPTED", "REJECTED"],
    if_match: str | None,
) -> PatchDecided:
    """Apply the decision, then send the existing `retry` (the same key: a repeat is one signal).
    The decision is committed before the signal, so if the signal fails the FDE can press retry and
    still start from the decided ISATVON."""
    client = await temporal_client(request)
    tenant_id = user.context(project_id).tenant_id
    with sessions() as session:
        seen = patch_decision.decided(session, user, project_id, workflow_id, sub_id, key)
    if not seen:
        await ensure_waiting(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            workflow_id=workflow_id,
            sub_skill_id=sub_id,
        )
        with sessions() as session:
            if decision == "ACCEPTED":
                patch_decision.accept(
                    session, user, project_id, workflow_id, sub_id, if_match, key, body.reason
                )
            else:
                patch_decision.reject(
                    session, user, project_id, workflow_id, sub_id, key, body.reason
                )
            session.commit()
    accepted = await retry_build(
        client,
        tenant_id=tenant_id,
        project_id=project_id,
        workflow_id=workflow_id,
        sub_skill_id=sub_id,
        retry=SubSkillRetry(
            reason=body.reason or f"ISATVON patch {decision.lower()}",
            idempotency_key=key,
            actor_id=str(user.user_id),
        ),
    )
    return PatchDecided(sub_skill_id=sub_id, build_no=accepted.build_no, decision=decision)


@router.post(HELD + "/accept", status_code=202)
async def accept_held_patch(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    request: Request,
    user: User,
    sessions: Sessions,
    if_match: IfMatch = None,
    idempotency_key: IdemKey = None,
    body: PatchDecision | None = None,
) -> PatchDecided:
    key = validate_idempotency_key(idempotency_key)
    return await _decide(
        request,
        sessions,
        user,
        project_id,
        workflow_id,
        sub_id,
        key,
        body or PatchDecision(),
        "ACCEPTED",
        if_match,
    )


@router.post(HELD + "/reject", status_code=202)
async def reject_held_patch(
    project_id: Project,
    workflow_id: UUID,
    sub_id: UUID,
    request: Request,
    user: User,
    sessions: Sessions,
    idempotency_key: IdemKey = None,
    body: PatchDecision | None = None,
) -> PatchDecided:
    key = validate_idempotency_key(idempotency_key)
    return await _decide(
        request,
        sessions,
        user,
        project_id,
        workflow_id,
        sub_id,
        key,
        body or PatchDecision(),
        "REJECTED",
        None,
    )
