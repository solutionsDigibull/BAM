"""POST W/generate (#678, TD §8 API table, §13.2): the FDE starts the build of a reviewed breakdown.

ProjectPipeline waits at WAIT_GENERATE for the `generate` signal (#79); this endpoint is what sends
it. Checks first, then the signal, then the audit:

* 404: another tenant's or an unassigned project (the route's guard), a Workflow of another
  project, or a `sub_skill_id` that is not one of this Workflow's Sub-Skills;
* 409 SOW_NOT_APPROVED when the Workflow's SOW version is not APPROVED; the Workflow's state as the
  editing rules answer it (409 WORKFLOW_BUILDING, 423 WORKFLOW_FROZEN, 409 CONFLICT), and only
  ISATVON_REVIEW can generate;
* 409 VALIDATION_ERROR naming every requested Sub-Skill that is not VALID. With no ids the request
  means every Sub-Skill of the Workflow, so all of them must be VALID (and there must be one);
* the signal goes out with `expect_stage=WAIT_GENERATE`: 409 PIPELINE_NOT_WAITING when the
  pipeline is anywhere else, 503 when Temporal is down. Nothing is recorded on a refusal.

202 with the project's pipeline run; audit GENERATE_REQUESTED (actor, Sub-Skills, run). Every
Sub-Skill sent to build takes the SUB_SKILL `generate` event (FDE, VALID -> BUILDING) in the same
transaction: flushed before the signal, committed after it, rolled back on a refusal (#682).
Without it every transition of the build (`gate_failed`, `tests_started`, ...) is refused.
A repeated Idempotency-Key answers the same run without a second signal or transition (and the
workflow drops a repeated key on its own). The workflow code is unchanged: it already applies
`generate` to the Workflow and builds.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sbam_contracts.api import ErrorCode, FieldError
from sbam_contracts.pipeline import Generate, PipelineStage
from sbam_contracts.runs import Run, pipeline_workflow_id
from sbam_contracts.state_machines import Actor, SowVersionState, SubSkillState, WorkflowState
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from sbam_api.audit.repository import find_by_key, record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.isatvon.edits import editable_workflow
from sbam_api.isatvon.models import SubSkill
from sbam_api.isatvon.repository import SubSkillRepository
from sbam_api.pipeline.service import TemporalUnavailable, signal_pipeline
from sbam_api.sow.repository import SowVersionRepository
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run

GENERATE_REQUESTED = "GENERATE_REQUESTED"
KEY_PREFIX = "generate:"


def _not_valid(skill_ids: list[str]) -> ApiError:
    return ApiError(
        409,
        ErrorCode.VALIDATION_ERROR,
        "Some Sub-Skills are not VALID",
        "Fix or regenerate them, validate, then generate again.",
        errors=[FieldError(path=f"sub_skills.{s}", message="not VALID") for s in skill_ids],
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
            f"The pipeline is not at the {PipelineStage.WAIT_GENERATE.value} step.",
        )
    return to_run(row)


def _prior(
    session: Session, user: CurrentUser, project_id: uuid.UUID, workflow_id: uuid.UUID, key: str
) -> Run | None:
    ctx = user.context(project_id)
    prior = find_by_key(session, ctx.tenant_id, "workflow", str(workflow_id), KEY_PREFIX + key)
    if prior is None or prior.action != GENERATE_REQUESTED or not prior.after:
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
    """The ids the signal carries (empty = all of them) and the Sub-Skills that will build, once
    every check passed."""
    ctx = user.context(project_id)
    workflow = editable_workflow(session, ctx, workflow_id)  # 404 / 409 / 423 by state
    if WorkflowState(workflow.state) is not WorkflowState.ISATVON_REVIEW:
        raise ApiError(
            409,
            ErrorCode.CONFLICT,
            "Cannot generate now",
            f"Generate starts from {WorkflowState.ISATVON_REVIEW.value}.",
        )
    sow = SowVersionRepository(session, ctx).get(workflow.sow_version_id)
    if sow is None or sow.state != SowVersionState.APPROVED.value:
        raise ApiError(
            409, ErrorCode.SOW_NOT_APPROVED, "SOW not approved", "Approve the SOW before building."
        )
    subs = {s.id: s for s in SubSkillRepository(session, ctx).of_workflow(workflow_id)}
    if any(i not in subs for i in requested):
        raise not_found()
    chosen = [subs[i] for i in dict.fromkeys(requested)] if requested else list(subs.values())
    if not chosen:
        raise _not_valid([])
    invalid = [s.skill_id for s in chosen if s.state != SubSkillState.VALID.value]
    if invalid:
        raise _not_valid(sorted(invalid))
    return list(dict.fromkeys(requested)), chosen


def _enter_building(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    chosen: list[SubSkill],
    correlation_id: str,
) -> None:
    """VALID -> BUILDING for each Sub-Skill sent to build; flushed, committed by `_commit`."""
    ctx = user.context(project_id)
    for sub in chosen:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=SubSkill,
                entity_id=sub.id,
                event="generate",
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
        action=GENERATE_REQUESTED,
        entity_type="workflow",
        entity_id=str(workflow_id),
        correlation_id=correlation_id,
        after={"run_id": str(run.id), "sub_skill_ids": [str(i) for i in sub_skill_ids]},
        idempotency_key=KEY_PREFIX + key if key else None,
    )
    session.commit()


async def start_generate(
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
            await asyncio.to_thread(
                _enter_building, session, user, project_id, chosen, correlation_id
            )
            signal = Generate(
                sub_skill_ids=wanted,
                idempotency_key=idempotency_key or str(uuid7()),
                actor_id=str(user.user_id),
            )
            try:
                client = await get_client()
                await signal_pipeline(
                    client,
                    project_id,
                    "generate",
                    signal,
                    expect_stage=PipelineStage.WAIT_GENERATE,
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
