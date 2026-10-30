"""POST P/sow/regenerate (E6.3, E6.2-T07/T08): a new SOW version from the latest READY knowledge.

The pipeline start's flow (replay of a repeated Idempotency-Key, row lock, Temporal first, then
the database). Where the project is decides how the new version is generated:

* SOW_REVIEW: ProjectPipeline is waiting for the approval. `SowRegenerate` (`sow-<project_id>`)
  runs `generate_sow`; the project state does not move and the new version becomes current.
* SOW_GENERATION_FAILED: the pipeline has ended. `regenerate` moves the project back to
  SOW_GENERATING and ProjectPipeline starts again at its SOW stage (`resume`), so the approval
  that follows is waited for as usual.

409: SOW_APPROVED (the current version is approved; scope changes use P/sow/new-version),
SOW_GENERATING (one is running), NOT_IN_REVIEW (no SOW yet), KNOWLEDGE_NOT_READY,
PROJECT_ARCHIVED. The hint is the FDE's own short text: control characters are removed, it is
passed to the model as a data block, and only its length is audited.
"""

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable

from sbam_contracts.api import ErrorCode
from sbam_contracts.pipeline import (
    PROJECT_PIPELINE,
    PipelineInput,
    PipelineStage,
    ResumeState,
    SowGenerateInput,
)
from sbam_contracts.projects import ProjectStatus
from sbam_contracts.runs import Run, RunInput, SubjectType, TaskQueue, pipeline_workflow_id
from sbam_contracts.sow_api import SOW_REGENERATE, sow_workflow_id
from sbam_contracts.state_machines import Actor, KnowledgeVersionState, SowVersionState
from sbam_contracts.state_machines import ProjectPipelineState as P
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from sbam_api.audit.repository import find_by_key, record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.knowledge.repository import KnowledgeVersionRepository
from sbam_api.pipeline.service import PIPELINE_QUEUE, TemporalUnavailable
from sbam_api.projects.models import Project
from sbam_api.projects.repository import ProjectRepository
from sbam_api.projects.service import project_archived
from sbam_api.sow.repository import SowRepository, SowVersionRepository
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.temporal.service import PipelineRunning, StartSpec, start_run

REGENERATE_STARTED = "SOW_REGENERATE_STARTED"
KEY_PREFIX = "sow-regenerate:"
_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u2028-\u202e\u2066-\u2069]")


def clean_hint(hint: str | None) -> str | None:
    """Control and direction-override characters removed (tab and newline kept), trimmed."""
    if hint is None:
        return None
    return _CONTROL.sub("", hint).strip() or None


def generating() -> ApiError:
    return ApiError(409, ErrorCode.SOW_GENERATING, "A SOW generation is already running")


def _prior(session: Session, user: CurrentUser, project_id: uuid.UUID, key: str) -> Run | None:
    ctx = user.context(project_id)
    prior = find_by_key(session, ctx.tenant_id, "project", str(project_id), KEY_PREFIX + key)
    if prior is None or prior.action != REGENERATE_STARTED or not prior.after:
        return None
    row = TemporalRunRepository(session, ctx).get(uuid.UUID(prior.after["run_id"]))
    return to_run(row) if row is not None else None


def _check(session: Session, user: CurrentUser, project_id: uuid.UUID) -> tuple[Project, uuid.UUID]:
    """The locked project and the knowledge version to generate from."""
    ctx = user.context(project_id)
    project = ProjectRepository(session, user.context()).get_for_update(project_id)
    if project is None:
        raise not_found()
    if project.status == ProjectStatus.ARCHIVED.value:
        raise project_archived()
    sow = SowRepository(session, ctx).of_project()
    current = (
        SowVersionRepository(session, ctx).get(sow.current_version_id)
        if sow is not None and sow.current_version_id
        else None
    )
    if current is not None and current.state == SowVersionState.APPROVED.value:
        raise ApiError(
            409,
            ErrorCode.SOW_APPROVED,
            "The SOW is approved",
            "An approved SOW is not regenerated; start a new version for a scope change.",
        )
    if P(project.state) is P.SOW_GENERATING:
        raise generating()
    if P(project.state) not in (P.SOW_REVIEW, P.SOW_GENERATION_FAILED):
        raise ApiError(409, ErrorCode.NOT_IN_REVIEW, "There is no SOW to regenerate yet")
    ready = KnowledgeVersionRepository(session, ctx).in_state(KnowledgeVersionState.READY)
    if not ready:
        raise ApiError(409, ErrorCode.KNOWLEDGE_NOT_READY, "No READY knowledge version")
    return project, ready[-1].id


def _spec(
    user: CurrentUser,
    project: Project,
    knowledge_version_id: uuid.UUID,
    hint: str | None,
    correlation_id: str,
    with_search_attributes: bool,
) -> StartSpec:
    base = RunInput(
        tenant_id=project.tenant_id,
        project_id=project.id,
        run_id=uuid7(),
        correlation_id=correlation_id,
    )
    if P(project.state) is P.SOW_REVIEW:
        return StartSpec(
            workflow=SOW_REGENERATE,
            workflow_id=sow_workflow_id(project.id),
            queue=TaskQueue.LLM,
            subject_type=SubjectType.PROJECT,
            subject_id=project.id,
            stage="sow",
            input=SowGenerateInput(
                **base.model_dump(), ref_id=knowledge_version_id, hint=hint, fresh=True
            ),  # an explicit Regenerate always asks the model again (#595)
        )
    refs = {"knowledge": knowledge_version_id, "okf": knowledge_version_id}
    return StartSpec(
        workflow=PROJECT_PIPELINE,
        workflow_id=pipeline_workflow_id(project.id),
        queue=PIPELINE_QUEUE,
        subject_type=SubjectType.PROJECT,
        subject_id=project.id,
        stage=PipelineStage.SOW.value,
        input=PipelineInput(
            **base.model_dump(),
            actor_id=str(user.user_id),
            search_attributes=with_search_attributes,
            resume=ResumeState(stage=PipelineStage.SOW, refs=refs),
            sow_hint=hint,
        ),
    )


def _commit(
    session: Session,
    user: CurrentUser,
    project: Project,
    run: Run,
    resumed: bool,
    hint: str | None,
    key: str | None,
    correlation_id: str,
) -> None:
    ctx = user.context(project.id)
    if resumed:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=Project,
                entity_id=project.id,
                event="regenerate",
                actor=Actor.FDE,
                actor_id=str(user.user_id),
                correlation_id=correlation_id,
                idempotency_key=f"{run.id}:regenerate",
            ),
        )
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=REGENERATE_STARTED,
        entity_type="project",
        entity_id=str(project.id),
        correlation_id=correlation_id,
        after={"run_id": str(run.id), "hint_chars": len(hint or ""), "resumed": resumed},
        idempotency_key=KEY_PREFIX + key if key else None,
    )
    session.commit()


async def start_regenerate(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    hint: str | None,
    *,
    idempotency_key: str | None = None,
    with_search_attributes: bool = True,
) -> Run:
    correlation_id = correlation_id_var.get() or str(uuid7())
    hint = clean_hint(hint)
    with sessions() as session:
        try:
            if idempotency_key:
                replay = await asyncio.to_thread(_prior, session, user, project_id, idempotency_key)
                if replay is not None:
                    return replay
            project, kv = await asyncio.to_thread(_check, session, user, project_id)
            resumed = P(project.state) is P.SOW_GENERATION_FAILED
            spec = _spec(user, project, kv, hint, correlation_id, with_search_attributes)
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
                raise generating() from None
            except OrchestratorUnavailable:
                raise TemporalUnavailable() from None
            await asyncio.to_thread(
                _commit, session, user, project, run, resumed, hint, idempotency_key, correlation_id
            )
            return run
        except BaseException:
            await asyncio.to_thread(session.rollback)
            raise
