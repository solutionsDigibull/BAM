"""Workflow versions: the list and the patch route (#132, E14.4).

A frozen row is never edited, so "superseded" is derived from the newer version. A patch is opened
on a FROZEN Sub-Skill only (409 `NOT_FROZEN` otherwise, which also answers a patch asked for while
the Workflow is still being frozen): the Sub-Skill goes back to DRAFT, its Macro Skill and the
Workflow to BUILDING. The frozen files and rows stay as they are; the next freeze is PATCH + 1.
"""

import uuid

from sbam_contracts.api import ErrorCode
from sbam_contracts.state_machines import Actor, AgentState, SubSkillState, WorkflowState
from sbam_contracts.workflow_versions import (
    NewVersionRequest,
    NewVersionStarted,
    WorkflowVersion,
    WorkflowVersionList,
)
from sqlalchemy.orm import Session

from sbam_api.audit.repository import record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.logging import correlation_id_var
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.state_machine import SYSTEM_ACTOR_ID, TransitionCommand, apply_transition
from sbam_api.versioning import numbers
from sbam_api.versioning.freezer import PATCH_STARTED
from sbam_api.versioning.models import WorkflowVersionRow
from sbam_api.versioning.repository import WorkflowVersionRepository
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository


def not_frozen(what: str, state: str) -> ApiError:
    return ApiError(
        409,
        ErrorCode.NOT_FROZEN,
        "Not frozen",
        f"{what} is {state}; only a frozen version can start a new patch version.",
    )


def _view(row: WorkflowVersionRow, newer: bool) -> WorkflowVersion:
    return WorkflowVersion(
        id=row.id,
        workflow_id=row.workflow_id,
        version=row.version,
        sow_version_id=row.sow_version_id,
        workflow_manifest_id=row.workflow_manifest_id,
        change_reason=row.change_reason,
        frozen_at=row.frozen_at,
        superseded=newer,
    )


def list_versions(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID
) -> WorkflowVersionList:
    ctx = user.context(project_id)
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    repo = WorkflowVersionRepository(session, ctx)
    newest = max((r.major, r.patch) for r in repo.of_project()) if repo.of_project() else None
    rows = repo.of_workflow(workflow_id)
    return WorkflowVersionList(
        items=[_view(r, newest is not None and (r.major, r.patch) < newest) for r in rows]
    )


def start_patch(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID,
    body: NewVersionRequest,
    key: str,
) -> NewVersionStarted:
    ctx = user.context(project_id)
    workflow: Workflow | None = WorkflowRepository(session, ctx).get_for_update(workflow_id)
    sub: SubSkill | None = SubSkillRepository(session, ctx).get_for_update(sub_skill_id)
    if workflow is None or sub is None or sub.workflow_id != workflow_id:
        raise not_found()
    if sub.state != SubSkillState.FROZEN.value:
        raise not_frozen("The Sub-Skill", sub.state)
    taken = [r.version for r in WorkflowVersionRepository(session, ctx).of_workflow(workflow_id)]
    if workflow.state == WorkflowState.FROZEN.value:
        upcoming = (
            numbers.next_patch(max(map(numbers.parse, taken)))
            if taken
            else numbers.next_patch(numbers.parse(workflow.version))
        )
    else:
        raise not_frozen("The Workflow", workflow.state)
    correlation = correlation_id_var.get() or "unknown"

    def move(model: type, entity_id: uuid.UUID, actor: Actor, actor_id: str, tag: str) -> None:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=model,
                entity_id=entity_id,
                event="start_patch_version",
                actor=actor,
                actor_id=actor_id,
                correlation_id=correlation,
                idempotency_key=f"patch:{key}:{tag}",
            ),
        )

    move(SubSkill, sub.id, Actor.FDE, str(user.user_id), "sub")
    macro: MacroSkill | None = MacroSkillRepository(session, ctx).get(sub.macro_skill_id)
    if macro is not None and macro.state == AgentState.FROZEN.value:
        move(MacroSkill, macro.id, Actor.SYSTEM, SYSTEM_ACTOR_ID, "macro")
    move(Workflow, workflow.id, Actor.FDE, str(user.user_id), "workflow")
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=PATCH_STARTED,
        entity_type="workflow",
        entity_id=str(workflow_id),
        correlation_id=correlation,
        after={
            "sub_skill_id": str(sub_skill_id),
            "next_version": str(upcoming),
            "change_reason": body.change_reason,
        },
        idempotency_key=f"{PATCH_STARTED}:{key}",
    )
    session.commit()
    return NewVersionStarted(
        workflow_id=workflow_id, sub_skill_id=sub_skill_id, next_version=str(upcoming)
    )
