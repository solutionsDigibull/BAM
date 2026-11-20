"""The FDE's repository question and answer (#756, ADR 0028 decision 1).

The question is the audit row `repo_question_asked` the build's `choose_repo` stage wrote when it
began to wait; the decision is the row `repo_decided` it writes once the FDE answered (or once one
candidate won outright). This module reads those rows for the workbench and turns the FDE's answer
into the `repo_answer` signal. It never writes the decision: the build does, so that who and when
are recorded in one place and the build only goes on after the record exists.

* A question is pending while the Sub-Skill is still BUILDING and its newest choice row is the
  question. A later decision, or a later build's question, replaces it.
* An answer is accepted only while the newest build of the Sub-Skill waits for it. A choice must be
  on the short list; when the catalogue had no candidate the FDE may name their own repository,
  which goes through the catalogue's URL rules (https only, no credentials, no private address).
* A Sub-Skill that is not in the Workflow, the project or the tenant is a 404, as everywhere.
"""

import uuid
from typing import Final

from pydantic import ValidationError
from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.build import SubSkillBuildStatus
from sbam_contracts.repo_choice import (
    DECIDED,
    QUESTION_ASKED,
    REPO_ANSWER_SIGNAL,
    PendingRepoQuestion,
    RepoAnswerAction,
    RepoAnswered,
    RepoAnswerRequest,
    RepoAnswerSignal,
    RepoQuestion,
    RepoQuestionKind,
    RepoQuestions,
)
from sbam_contracts.repo_decision import RepoDecision
from sbam_contracts.state_machines import SubSkillState
from sqlalchemy.orm import Session
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from sbam_api.audit.models import AuditLog
from sbam_api.audit.repository import entity_history
from sbam_api.builds.service import NotInReview, build_status, latest_build_id
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.repo_catalog.parse import parse_cell
from sbam_api.repo_choice.waiting import pending_row
from sbam_api.temporal.client import RPC_TIMEOUT, OrchestratorUnavailable
from sbam_api.workflows.repository import WorkflowRepository

ENTITY: Final = "sub_skill"
_ACTIONS: Final = (QUESTION_ASKED, DECIDED)


def _workflow_subs(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> list[SubSkill]:
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    subs: list[SubSkill] = SubSkillRepository(session, ctx).of_workflow(workflow_id)
    return subs


def _newest_rows(
    session: Session, ctx: TenantContext, subs: list[SubSkill]
) -> dict[str, dict[str, AuditLog]]:
    """Per Sub-Skill id: the newest `repo_question_asked` and the newest `repo_decided` row."""
    found: dict[str, dict[str, AuditLog]] = {}
    for sub in subs:
        for row in entity_history(session, ctx.tenant_id, ENTITY, str(sub.id)):
            if row.action in _ACTIONS:  # oldest first, so the last one of an action wins
                found.setdefault(str(sub.id), {})[row.action] = row
    return found


def _build_no(row: AuditLog) -> int | None:
    """The build the row belongs to: its idempotency key ends with `:<build_no>`."""
    tail = (row.idempotency_key or "").rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


def questions(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID
) -> RepoQuestions:
    ctx = user.context(project_id)
    subs = _workflow_subs(session, ctx, workflow_id)
    rows = _newest_rows(session, ctx, subs)
    pending: list[PendingRepoQuestion] = []
    decisions: list[RepoDecision] = []
    for sub in subs:
        mine = rows.get(str(sub.id), {})
        decided = mine.get(DECIDED)
        if decided is not None and decided.after is not None:
            try:
                decisions.append(RepoDecision.model_validate(decided.after, strict=False))
            except ValidationError:
                pass  # a row this version cannot read is skipped, never shown half-way
        asked = pending_row(mine)
        if asked is None or sub.state != SubSkillState.BUILDING:
            continue
        try:
            question = RepoQuestion.model_validate(asked.after, strict=False)
        except ValidationError:
            continue
        pending.append(
            PendingRepoQuestion(
                question=question, sub_skill_name=sub.skill_name, build_no=_build_no(asked)
            )
        )
    return RepoQuestions(workflow_id=str(workflow_id), questions=pending, decisions=decisions)


def _field_error(message: str) -> ApiError:
    return ApiError(
        422,
        ErrorCode.VALIDATION_ERROR,
        "Invalid answer",
        errors=[FieldError(path="url", message=message)],
    )


def checked_answer(question: RepoQuestion, body: RepoAnswerRequest) -> RepoAnswerRequest:
    """The answer, with the FDE's own URL in the catalogue's canonical form. 422 when a choice is
    not allowed for this question."""
    if body.action is RepoAnswerAction.NO_REPO:
        return body
    if body.url is None:  # RepoAnswerRequest refuses this for CHOOSE; belt and braces
        raise _field_error("Choose one of the repositories on the short list.")
    if body.url in {c.url for c in question.candidates}:
        return body
    if question.kind is not RepoQuestionKind.NO_CANDIDATE:
        raise _field_error("Choose one of the repositories on the short list, or say none fits.")
    found = parse_cell(body.url)
    if len(found) != 1 or found[0].url is None or found[0].reason is not None:
        raise _field_error("That is not a repository address SBAM can use (https, no sign-in).")
    return RepoAnswerRequest(action=RepoAnswerAction.CHOOSE, url=found[0].url)


def _pending_question(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, sub_id: uuid.UUID
) -> RepoQuestion | None:
    subs = _workflow_subs(session, ctx, workflow_id)
    sub = next((s for s in subs if s.id == sub_id), None)
    if sub is None:
        raise not_found()
    asked = pending_row(_newest_rows(session, ctx, [sub]).get(str(sub.id), {}))
    if asked is None:
        return None
    try:
        return RepoQuestion.model_validate(asked.after, strict=False)
    except ValidationError:
        return None


def _is_not_this(status: SubSkillBuildStatus, ids: tuple[uuid.UUID, ...]) -> bool:
    return (status.tenant_id, status.project_id, status.workflow_id, status.sub_skill_id) != ids


async def answer_question(
    client: Client,
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
    key: str,
    body: RepoAnswerRequest,
) -> RepoAnswered:
    """Signal the FDE's answer to the Sub-Skill's newest build; the build records the decision."""
    ctx = user.context(project_id)
    question = _pending_question(session, ctx, workflow_id, sub_id)
    build_id = await latest_build_id(client, sub_id)
    if build_id is None:
        raise not_found()
    status = await build_status(client, build_id)
    if status is None:
        raise NotInReview()
    if _is_not_this(status, (ctx.tenant_id, project_id, workflow_id, sub_id)):
        raise not_found()
    accepted = RepoAnswered(sub_skill_id=sub_id, build_no=status.build_no)
    if key in status.repo_answer_keys:
        return accepted  # this request was already taken: same answer, no second signal
    if not status.waiting_for_repo_choice or question is None:
        raise NotInReview()
    answer = checked_answer(question, body)
    signal = RepoAnswerSignal(
        action=answer.action, url=answer.url, idempotency_key=key, actor_id=str(user.user_id)
    )
    try:
        await client.get_workflow_handle(build_id).signal(
            REPO_ANSWER_SIGNAL, signal, rpc_timeout=RPC_TIMEOUT
        )
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:  # finished between the query and the signal
            raise NotInReview() from None
        raise OrchestratorUnavailable() from None
    return accepted
