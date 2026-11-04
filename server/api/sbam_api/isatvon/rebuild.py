"""POST W/rebuild (#695, TD §8 API table): the FDE rebuilds Sub-Skills that need review.

After a build round with failed Sub-Skills ProjectPipeline waits at WAIT_REBUILD for the
`rebuild_subskills` signal (#79); this endpoint is what sends it, as `generate.py` sends `generate`:

* 404: another tenant's or an unassigned project (the route's guard), a Workflow of another
  project, or a `sub_skill_id` that is not one of this Workflow's Sub-Skills;
* 423 WORKFLOW_FROZEN, otherwise 409 CONFLICT unless the Workflow is NEEDS_FDE_REVIEW (the pipeline
  itself applies the Workflow's `retry` when it gets the signal, so this route does not);
* 409 VALIDATION_ERROR naming every requested Sub-Skill whose state has no FDE `retry` event into
  BUILDING (NEEDS_FDE_REVIEW and DEPENDENCY_FAILED have one). With no ids the request means every
  Sub-Skill of the Workflow in such a state, and there must be one;
* the signal goes out with `expect_stage=WAIT_REBUILD`: 409 PIPELINE_NOT_WAITING when the pipeline
  is anywhere else, 503 when Temporal is down. Nothing is recorded on a refusal.

202 with the project's pipeline run; audit REBUILD_REQUESTED (actor, Sub-Skills, run). Every
Sub-Skill sent to rebuild takes the SUB_SKILL `retry` event (FDE) in the same transaction: flushed
before the signal, committed after it, rolled back on a refusal, so the build's own events are not
refused. A repeated Idempotency-Key answers the same run without a second signal or transition.
The workflow code is unchanged: it already handles the signal.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.pipeline import PipelineStage, RebuildSubSkills
from sbam_contracts.runs import Run, pipeline_workflow_id
from sbam_contracts.state_machines import Actor, SubSkillState, WorkflowState
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.audit.repository import find_by_key, record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.pipeline.service import TemporalUnavailable, signal_pipeline
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.workflows.repository import WorkflowRepository

REBUILD_REQUESTED = "REBUILD_REQUESTED"
KEY_PREFIX = "rebuild:"


REBUILDABLE = frozenset(
    {SubSkillState.NEEDS_FDE_REVIEW.value, SubSkillState.DEPENDENCY_FAILED.value}
)


def _not_rebuildable(skill_ids: list[str]) -> ApiError:
    return ApiError(
        409,
        ErrorCode.VALIDATION_ERROR,
        "Some Sub-Skills cannot be rebuilt",
        "Only Sub-Skills that need review can be rebuilt.",
        errors=[
            FieldError(path=f"sub_skills.{s}", message="not waiting for review") for s in skill_ids
        ],
    )


def _pipeline_run(session: Session, user: CurrentUser, project_id: uuid.UUID) -> Run:
    row = TemporalRunRepository(session, user.context(project_id)).latest_for_workflow_id(
        pipeline_workflow_id(project_id)
    )
    if row is None:
        raise ApiError(
            409,
            ErrorCode.PIPELINE_NOT_WAITING,
            "Pipeline is not waiting for this",
            f"The pipeline is not at the {PipelineStage.WAIT_REBUILD.value} step.",
        )
    return to_run(row)


def _prior(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID, key: str
) -> Run | None:
    ctx = user.context(project_id)
    prior = find_by_key(session, ctx.tenant_id, "workflow", str(workflow_id), KEY_PREFIX + key)
    if prior is None or prior.action != REBUILD_REQUESTED or not prior.after:
        return None
    row = TemporalRunRepository(session, ctx).get(uuid.UUID(prior.after["run_id"]))
    return to_run(row) if row is not None else None


def _check(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    requested: list[uuid.UUID],
) -> tuple[list[uuid.UUID], list[SubSkill]]:
    """The ids the signal carries and the Sub-Skills that will build, once
    every check passed."""
    ctx = user.context(project_id)
    workflow = WorkflowRepository(session, ctx).get(workflow_id)
    if workflow is None:
        raise not_found()
    state = WorkflowState(workflow.state)
    if state is WorkflowState.FROZEN:
        raise ApiError(
            423, ErrorCode.WORKFLOW_FROZEN, "Workflow frozen", "Create a new version to change it."
        )
    if state is not WorkflowState.NEEDS_FDE_REVIEW:
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "Cannot rebuild now",
            f"Rebuild starts from {WorkflowState.NEEDS_FDE_REVIEW.value}.",
        )
    subs = {s.id: s for s in SubSkillRepository(session, ctx).of_workflow(workflow_id)}
    if any(i not in subs for i in requested):
        raise not_found()
    if requested:
        chosen = [subs[i] for i in dict.fromkeys(requested)]
        invalid = [s.skill_id for s in chosen if s.state not in REBUILDABLE]
        if invalid:
            raise _not_rebuildable(sorted(invalid))
    else:
        chosen = [s for s in subs.values() if s.state in REBUILDABLE]
        if not chosen:
            raise _not_rebuildable([])
    # The signal needs ids (empty is refused by its model): resolve "all" to the chosen ones.
    return [s.id for s in chosen], chosen


def _retry(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    chosen: list[SubSkill],
    correlation_id: str,
) -> None:
    """`retry` (to BUILDING) for each Sub-Skill sent to rebuild; flushed, committed by `_commit`."""
    ctx = user.context(project_id)
    for sub in chosen:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=SubSkill,
                entity_id=sub.id,
                event="retry",
                actor=Actor.FDE,
                actor_id=str(user.user_id),
                correlation_id=correlation_id,
            ),
        )


def _commit(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    run: Run,
    sub_skill_ids: list[uuid.UUID],
    key: str | None,
    correlation_id: str,
) -> None:
    ctx = user.context(project_id)
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=REBUILD_REQUESTED,
        entity_type="workflow",
        entity_id=str(workflow_id),
        correlation_id=correlation_id,
        after={"run_id": str(run.id), "sub_skill_ids": [str(i) for i in sub_skill_ids]},
        idempotency_key=KEY_PREFIX + key if key else None,
    )
    session.commit()


async def start_rebuild(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_skill_ids: list[uuid.UUID],
    *,
    idempotency_key: str | None = None,
) -> Run:
    correlation_id = correlation_id_var.get() or str(uuid7())
    with sessions() as session:
        try:
            if idempotency_key:
                replay = await asyncio.to_thread(
                    _prior, session, user, project_id, workflow_id, idempotency_key
                )
                if replay is not None:
                    return replay
            wanted, chosen = await asyncio.to_thread(
                _check, session, user, project_id, workflow_id, sub_skill_ids
            )
            run = await asyncio.to_thread(_pipeline_run, session, user, project_id)
            await asyncio.to_thread(_retry, session, user, project_id, chosen, correlation_id)
            signal = RebuildSubSkills(
                sub_skill_ids=wanted,
                idempotency_key=idempotency_key or str(uuid7()),
                actor_id=str(user.user_id),
            )
            try:
                client = await get_client()
                await signal_pipeline(
                    client,
                    project_id,
                    "rebuild_subskills",
                    signal,
                    expect_stage=PipelineStage.WAIT_REBUILD,
                )
            except OrchestratorUnavailable:
                raise TemporalUnavailable() from None
            await asyncio.to_thread(
                _commit,
                session,
                user,
                project_id,
                workflow_id,
                run,
                wanted,
                idempotency_key,
                correlation_id,
            )
            return run
        except BaseException:
            await asyncio.to_thread(session.rollback)
            raise
