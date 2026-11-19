"""POST/GET W/request-change (#767, WG-19): the FDE's plain-words note about the generated code.

Order of a POST (nothing is stored on a refusal before the note itself):

1. 404 for a Workflow or Sub-Skill outside this tenant and project (the route's guard and the
   tenant-scoped repositories); a repeated `Idempotency-Key` answers the note it stored (422 when
   the key was used for another Sub-Skill).
2. 409 once the Workflow version already has `MAX_NOTES_PER_VERSION` notes: the guidance points to
   the ISATVON Grid, where the FDE changes what a Sub-Skill means.
3. The note is stored in the audit trail (and committed) before anything reads it.
4. A Sub-Skill whose build waits for the FDE (NEEDS_FDE_REVIEW) starts `RequestChangeApply`, which
   classifies the note and either proposes an ISATVON patch (the held-patch flow) or has the coder
   rebuild with the note as data. Any other state (certified, a committed Workflow in Staging) has
   no build to hand it to: the note is QUEUED for the next generated version.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sbam_contracts.api import ErrorCode
from sbam_contracts.request_change import (
    MAX_NOTES_PER_VERSION,
    REQUEST_CHANGE_APPLY,
    ChangeNote,
    ChangeNoteList,
    ChangeNoteStatus,
    RequestChangeAccepted,
    RequestChangeCreate,
    RequestChangeInput,
    request_change_workflow_id,
)
from sbam_contracts.runs import Run, SubjectType, TaskQueue
from sbam_contracts.state_machines import SubSkillState
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from sbam_api.builds.service import build_status, latest_build_id
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.pipeline.service import TemporalUnavailable
from sbam_api.request_change import notes
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.service import PipelineRunning, StartSpec, start_run
from sbam_api.workflows.models import Workflow
from sbam_api.workflows.repository import WorkflowRepository

NOT_WAITING = "No build is waiting for review; the note is kept for the next generated version."
UNAVAILABLE = "The orchestrator was unavailable; send the note again."
BUSY = "Another note was being applied; send this one again."


def limit_reached() -> ApiError:
    return ApiError(
        409,
        ErrorCode.CONFLICT,
        "Request-change limit reached",
        f"This workflow version already has {MAX_NOTES_PER_VERSION} change notes. "
        "Change what the Sub-Skill means in the ISATVON Grid instead.",
    )


def _running() -> ApiError:
    return ApiError(
        409,
        ErrorCode.CONFLICT,
        "A change note is already being applied",
        "Wait for it to finish before sending the next note.",
    )


def _targets(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID | None,
) -> tuple[Workflow, SubSkill | None]:
    ctx = user.context(project_id)
    workflow: Workflow | None = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    sub: SubSkill | None = None
    if sub_skill_id is not None:
        sub = SubSkillRepository(session, ctx).get(sub_skill_id)
        if sub is None or sub.workflow_id != workflow_id:
            raise not_found()
    return workflow, sub


def list_notes(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID
) -> ChangeNoteList:
    workflow, _ = _targets(session, user, project_id, workflow_id, None)
    tenant_id = user.context(project_id).tenant_id
    found = notes.notes_of(session, tenant_id, workflow_id, workflow.version)
    return ChangeNoteList(notes=found, used=len(found))


def _store(
    sessions: sessionmaker[Session],
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    body: RequestChangeCreate,
    key: str,
    correlation_id: str,
) -> tuple[ChangeNote, bool, uuid.UUID]:
    """(the note, whether it is new, the tenant). Commits the note."""
    with sessions() as session:
        workflow, sub = _targets(session, user, project_id, workflow_id, body.sub_skill_id)
        if sub is None:  # a Sub-Skill id was given, so `_targets` found it or raised
            raise not_found()
        tenant_id = user.context(project_id).tenant_id
        prior = notes.find_note(session, tenant_id, workflow_id, key)
        if prior is not None:
            after = prior.after or {}
            if after.get("sub_skill_id") != str(body.sub_skill_id):
                raise ApiError(
                    422,
                    ErrorCode.IDEMPOTENCY_KEY_REUSED,
                    "Idempotency-Key already used with a different request",
                )
            existing = notes.get_note(
                session,
                tenant_id,
                workflow_id,
                str(after["workflow_version"]),
                uuid.UUID(after["note_id"]),
            )
            if existing is None:
                raise not_found()
            return existing, False, tenant_id
        if notes.count(session, tenant_id, workflow_id, workflow.version) >= MAX_NOTES_PER_VERSION:
            raise limit_reached()
        note_id = notes.record_note(
            session,
            tenant_id=tenant_id,
            actor_id=str(user.user_id),
            workflow_id=workflow_id,
            sub_skill_id=body.sub_skill_id,
            version=workflow.version,
            text=body.note,
            key=key,
            correlation_id=correlation_id,
        )
        session.commit()
        created = notes.get_note(session, tenant_id, workflow_id, workflow.version, note_id)
        if created is None:
            raise not_found()
        return created, True, tenant_id


def _outcome(
    sessions: sessionmaker[Session],
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    note: ChangeNote,
    status: ChangeNoteStatus,
    reason: str,
    correlation_id: str,
) -> ChangeNote:
    with sessions() as session:
        notes.record_outcome(
            session,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            note_id=note.id,
            status=status,
            reason=reason,
            correlation_id=correlation_id,
        )
        session.commit()
        found = notes.get_note(session, tenant_id, workflow_id, note.workflow_version, note.id)
        if found is None:
            raise not_found()
        return found


def _sub_state(
    sessions: sessionmaker[Session], user: CurrentUser, project_id: uuid.UUID, sub_id: uuid.UUID
) -> str:
    with sessions() as session:
        sub = SubSkillRepository(session, user.context(project_id)).get(sub_id)
        return "" if sub is None else str(sub.state)


async def _waiting_build(
    client: Client,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_id: uuid.UUID,
) -> str | None:
    """The id of the Sub-Skill's newest build when it waits for the FDE, else None."""
    build_id = await latest_build_id(client, sub_id)
    if build_id is None:
        return None
    status = await build_status(client, build_id)
    if status is None or not status.waiting_for_retry:
        return None
    ours = (tenant_id, project_id, workflow_id, sub_id)
    if (status.tenant_id, status.project_id, status.workflow_id, status.sub_skill_id) != ours:
        return None
    return build_id


async def request_change(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    body: RequestChangeCreate,
    key: str,
) -> RequestChangeAccepted:
    correlation_id = correlation_id_var.get() or str(uuid7())
    note, is_new, tenant_id = await asyncio.to_thread(
        _store, sessions, user, project_id, workflow_id, body, key, correlation_id
    )
    if not is_new:
        return RequestChangeAccepted(note=note)

    async def settle(status: ChangeNoteStatus, reason: str) -> ChangeNote:
        return await asyncio.to_thread(
            _outcome, sessions, tenant_id, workflow_id, note, status, reason, correlation_id
        )

    state = await asyncio.to_thread(_sub_state, sessions, user, project_id, body.sub_skill_id)
    if state != SubSkillState.NEEDS_FDE_REVIEW.value:
        return RequestChangeAccepted(note=await settle(ChangeNoteStatus.QUEUED, NOT_WAITING))
    try:
        client = await get_client()
        build_id = await _waiting_build(
            client, tenant_id, project_id, workflow_id, body.sub_skill_id
        )
    except OrchestratorUnavailable:
        await settle(ChangeNoteStatus.PARKED, UNAVAILABLE)
        raise TemporalUnavailable() from None
    if build_id is None:
        return RequestChangeAccepted(note=await settle(ChangeNoteStatus.QUEUED, NOT_WAITING))
    spec = StartSpec(
        workflow=REQUEST_CHANGE_APPLY,
        workflow_id=request_change_workflow_id(workflow_id),
        queue=TaskQueue.LLM,
        subject_type=SubjectType.SUB_SKILL,
        subject_id=body.sub_skill_id,
        stage="request_change",
        input=RequestChangeInput(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=uuid7(),
            correlation_id=correlation_id,
            workflow_id=workflow_id,
            sub_skill_id=body.sub_skill_id,
            note_id=note.id,
            build_workflow_id=build_id,
            actor_id=str(user.user_id),
        ),
    )
    try:
        run: Run = await start_run(client, sessions, spec, conflict=WorkflowIDConflictPolicy.FAIL)
    except PipelineRunning:
        await settle(ChangeNoteStatus.PARKED, BUSY)
        raise _running() from None
    except OrchestratorUnavailable:
        await settle(ChangeNoteStatus.PARKED, UNAVAILABLE)
        raise TemporalUnavailable() from None
    return RequestChangeAccepted(note=note, run=run)
