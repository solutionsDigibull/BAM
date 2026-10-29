"""POST W/macro-skills/{id}/regenerate and POST W/sub-skills/{id}/regenerate (E8.4).

The FDE asks for a Macro Skill's Sub-Skills (or one Sub-Skill) to be written again, with an optional
short hint. `IsatvonRegenerate` (`isatvon-regen-<macro_id>`) does the work in the LLM worker; this
module only checks and starts it: 202 with a `Run`. A Sub-Skill and its Macro Skill share one
workflow id, so only one regeneration of a Macro Skill runs at a time (409 `CONFLICT`).

Same flow as `sow/regenerate.py`: replay of a repeated Idempotency-Key, row lock, Temporal first,
then the database. 404 for another tenant's, an unassigned or a foreign-Workflow entity; 409 / 423
by the Workflow's state (`editable_workflow`); 409 when a Sub-Skill of the Macro Skill is certified
or frozen. The hint's control characters are removed and only its length is audited.

The run is recorded under the Macro Skill, so a repeated Idempotency-Key replays on both routes;
the same key for a different target (another Sub-Skill, or the Macro Skill) is 422
`IDEMPOTENCY_KEY_REUSED`.
"""

import asyncio
import functools
import uuid
from collections.abc import Awaitable, Callable

from sbam_contracts.api import ErrorCode
from sbam_contracts.isatvon_workbench import (
    ISATVON_REGENERATE,
    RegenerateInput,
    regenerate_workflow_id,
)
from sbam_contracts.runs import Run, SubjectType, TaskQueue
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from sbam_api.audit.repository import find_by_key, record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.isatvon.edits import SUB_SKILL_LOCKED, editable_workflow
from sbam_api.isatvon.repository import MacroSkillRepository, SubSkillRepository
from sbam_api.pipeline.service import TemporalUnavailable
from sbam_api.sow.regenerate import clean_hint
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.temporal.service import PipelineRunning, StartSpec, start_run

REGENERATE_STARTED = "ISATVON_REGENERATE_STARTED"
KEY_PREFIX = "isatvon-regenerate:"


def _running() -> ApiError:
    return ApiError(
        409,
        ErrorCode.CONFLICT,
        "A regeneration is already running",
        "Wait for it to finish before asking again.",
    )


def _prior(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    entity_id: uuid.UUID,
    key: str,
    *,
    is_sub: bool,
) -> Run | None:
    """The run a repeated key already started. `_commit` records it under the Macro Skill, so a
    Sub-Skill's id is first turned into its macro's; the key may only repeat the same target."""
    ctx = user.context(project_id)
    macro_id, sub_id = entity_id, None
    if is_sub:
        sub = SubSkillRepository(session, ctx).get(entity_id)
        if sub is None or sub.workflow_id != workflow_id:
            return None  # `_check` answers 404
        macro_id, sub_id = sub.macro_skill_id, sub.id
    prior = find_by_key(session, ctx.tenant_id, "macro_skill", str(macro_id), KEY_PREFIX + key)
    if prior is None or prior.action != REGENERATE_STARTED or not prior.after:
        return None
    if prior.after.get("sub_skill_id") != (str(sub_id) if sub_id else None):
        raise ApiError(
            422,
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "Idempotency-Key already used with a different request",
        )
    row = TemporalRunRepository(session, ctx).get(uuid.UUID(prior.after["run_id"]))
    return to_run(row) if row is not None else None


def _check(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    entity_id: uuid.UUID,
    is_sub: bool,
) -> tuple[uuid.UUID, uuid.UUID | None, uuid.UUID]:
    """(macro id, sub id or None, tenant id) of an entity that may be regenerated."""
    ctx = user.context(project_id)
    editable_workflow(session, ctx, workflow_id)
    macros = MacroSkillRepository(session, ctx)
    subs = SubSkillRepository(session, ctx)
    sub_id: uuid.UUID | None = None
    if is_sub:
        sub = subs.get(entity_id)
        if sub is None or sub.workflow_id != workflow_id:
            raise not_found()
        sub_id, macro_id = sub.id, sub.macro_skill_id
    else:
        macro_id = entity_id
    macro = macros.get_for_update(macro_id)
    if macro is None or macro.workflow_id != workflow_id:
        raise not_found()
    if any(s.state in SUB_SKILL_LOCKED for s in subs.of_macro(macro_id)):
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "Cannot regenerate",
            "A certified or frozen Sub-Skill is kept as it is.",
        )
    return macro_id, sub_id, ctx.tenant_id


def _commit(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    macro_id: uuid.UUID,
    sub_id: uuid.UUID | None,
    run: Run,
    hint: str | None,
    key: str | None,
    correlation_id: str,
) -> None:
    ctx = user.context(project_id)
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=REGENERATE_STARTED,
        entity_type="macro_skill",
        entity_id=str(macro_id),
        correlation_id=correlation_id,
        after={
            "run_id": str(run.id),
            "hint_chars": len(hint or ""),
            "sub_skill_id": str(sub_id) if sub_id else None,
        },
        idempotency_key=KEY_PREFIX + key if key else None,
    )
    session.commit()


async def start_regenerate(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    entity_id: uuid.UUID,
    hint: str | None,
    *,
    is_sub: bool,
    idempotency_key: str | None = None,
    with_search_attributes: bool = True,
) -> Run:
    correlation_id = correlation_id_var.get() or str(uuid7())
    hint = clean_hint(hint)
    with sessions() as session:
        try:
            if idempotency_key:
                replay = await asyncio.to_thread(
                    functools.partial(
                        _prior,
                        session,
                        user,
                        project_id,
                        workflow_id,
                        entity_id,
                        idempotency_key,
                        is_sub=is_sub,
                    )
                )
                if replay is not None:
                    return replay
            macro_id, sub_id, tenant_id = await asyncio.to_thread(
                _check, session, user, project_id, workflow_id, entity_id, is_sub
            )
            spec = StartSpec(
                workflow=ISATVON_REGENERATE,
                workflow_id=regenerate_workflow_id(macro_id),
                queue=TaskQueue.LLM,
                subject_type=SubjectType.SUB_SKILL,
                subject_id=sub_id or macro_id,
                stage="isatvon",
                input=RegenerateInput(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    run_id=uuid7(),
                    correlation_id=correlation_id,
                    workflow_id=workflow_id,
                    macro_skill_id=macro_id,
                    sub_skill_id=sub_id,
                    hint=hint,
                    actor_id=str(user.user_id),
                ),
            )
            try:
                client = await get_client()
            except OrchestratorUnavailable:
                raise TemporalUnavailable() from None
            try:
                run = await start_run(
                    client,
                    sessions,
                    spec,
                    with_search_attributes=with_search_attributes,
                    conflict=WorkflowIDConflictPolicy.FAIL,
                )
            except PipelineRunning:
                raise _running() from None
            except OrchestratorUnavailable:
                raise TemporalUnavailable() from None
            await asyncio.to_thread(
                _commit,
                session,
                user,
                project_id,
                macro_id,
                sub_id,
                run,
                hint,
                idempotency_key,
                correlation_id,
            )
            return run
        except BaseException:
            await asyncio.to_thread(session.rollback)
            raise
