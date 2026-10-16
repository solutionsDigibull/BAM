"""Knowledge versions (E5.2, TD §7, §13.2): allocate, record sources, finish, rebuild.

* `allocate_version`: the version a run builds, numbered per project. A retry of the same run
  finds the version it allocated (`run_id` is unique), so it never allocates another. Under the
  project row lock; a second BUILDING version is refused (409 KNOWLEDGE_BUILDING), and the DB's
  partial unique index refuses it too.
* `finish_version`: BUILDING -> READY supersedes the previous READY version (-> OUTDATED);
  BUILDING -> FAILED leaves it usable. Both through the state-machine service (audit row).
* `start_rebuild` (POST P/knowledge/rebuild): the pipeline start's flow (replay of a repeated
  Idempotency-Key, row lock, Temporal first, then the database), for the `KnowledgeRebuild`
  workflow. A rebuild builds a new version only; it never moves the project's pipeline state.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sbam_contracts.api import ErrorCode
from sbam_contracts.knowledge import (
    KNOWLEDGE_REBUILD,
    CoverageEntry,
    KnowledgeCoverage,
    KnowledgeVersionOut,
    SourceStatus,
    cognee_dataset,
    knowledge_workflow_id,
)
from sbam_contracts.projects import ProjectStatus
from sbam_contracts.runs import Run, RunInput, SubjectType, TaskQueue
from sbam_contracts.state_machines import Actor, KnowledgeVersionState, MachineName
from sbam_contracts.state_machines import ProjectPipelineState as P
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

from sbam_api.audit.repository import find_by_key, record
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import ApiError, not_found
from sbam_api.core.ids import uuid7
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.repository import TenantContext
from sbam_api.knowledge.models import KnowledgeSource, KnowledgeVersion
from sbam_api.knowledge.repository import KnowledgeSourceRepository, KnowledgeVersionRepository
from sbam_api.pipeline.service import TemporalUnavailable
from sbam_api.projects.repository import ProjectRepository
from sbam_api.projects.service import project_archived
from sbam_api.state_machine import TransitionCommand, apply_transition
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID, apply_create
from sbam_api.temporal.client import OrchestratorUnavailable
from sbam_api.temporal.repository import TemporalRunRepository, to_run
from sbam_api.temporal.service import PipelineRunning, StartSpec, start_run

REBUILD_STARTED = "KNOWLEDGE_REBUILD_STARTED"
KEY_PREFIX = "knowledge-rebuild:"
# The pipeline's own knowledge stage is (about to be) running: a rebuild would race it.
PIPELINE_BUILDING = frozenset({P.INGESTING, P.KNOWLEDGE_BUILDING})


class KnowledgeBuilding(ApiError):  # noqa: N818 - code name from the E5.2 spec
    def __init__(self) -> None:
        super().__init__(
            409,
            ErrorCode.KNOWLEDGE_BUILDING,
            "Knowledge is being built",
            "A knowledge version is already being built for this project; try again when it ends.",
        )


def _key(kv: KnowledgeVersion, event: str, suffix: str = "") -> str:
    return f"{kv.run_id}:{MachineName.KNOWLEDGE_VERSION.value}:{event}{suffix}"


def allocate_version(
    session: Session,
    ctx: TenantContext,
    *,
    run_id: uuid.UUID,
    event: str,
    actor: Actor,
    actor_id: str,
    correlation_id: str,
) -> KnowledgeVersion:
    """The BUILDING version of `run_id`, created on the first call. Does not commit."""
    project_id = ctx.project_id
    if project_id is None:
        raise ValueError("allocate_version needs a project in the context")
    repo = KnowledgeVersionRepository(session, ctx)
    mine = repo.by_run(run_id)
    if mine is not None:
        return mine
    if ProjectRepository(session, TenantContext(ctx.tenant_id)).get_for_update(project_id) is None:
        raise not_found()
    if repo.in_state(KnowledgeVersionState.BUILDING):
        raise KnowledgeBuilding()
    version = KnowledgeVersion(
        id=uuid7(),
        version=repo.next_number(),
        run_id=run_id,
        cognee_dataset=cognee_dataset(ctx.tenant_id, project_id),
        coverage=KnowledgeCoverage().model_dump(mode="json"),
    )
    apply_create(
        session,
        ctx,
        version,
        event=event,
        actor=actor,
        actor_id=actor_id,
        correlation_id=correlation_id,
        idempotency_key=f"{run_id}:{MachineName.KNOWLEDGE_VERSION.value}:{event}",
    )
    return version


def record_source(
    session: Session,
    ctx: TenantContext,
    version: KnowledgeVersion,
    entry: CoverageEntry,
    sha256: str | None = None,
) -> None:
    """One `knowledge_sources` row per document version; a retry updates it. Does not commit."""
    repo = KnowledgeSourceRepository(session, ctx)
    row = repo.find(version.id, entry.document_version_id)
    if row is None:
        repo.add(
            KnowledgeSource(
                knowledge_version_id=version.id,
                document_version_id=entry.document_version_id,
                status=entry.status.value,
                sha256=sha256,
                reason=entry.reason,
            )
        )
        return
    row.status, row.sha256, row.reason = entry.status.value, sha256, entry.reason
    session.flush()


def finish_version(
    session: Session,
    ctx: TenantContext,
    version: KnowledgeVersion,
    *,
    ready: bool,
    coverage: KnowledgeCoverage,
    correlation_id: str,
    error_reason: str | None = None,
) -> None:
    """BUILDING -> READY (previous READY -> OUTDATED) or -> FAILED. Idempotent; does not commit."""
    version.coverage = coverage.model_dump(mode="json")
    version.error_reason = error_reason
    session.flush()

    def move(entity: KnowledgeVersion, event: str, key: str) -> None:
        apply_transition(
            session,
            ctx,
            TransitionCommand(
                model=KnowledgeVersion,
                entity_id=entity.id,
                event=event,
                actor=Actor.SYSTEM,
                actor_id=SYSTEM_ACTOR_ID,
                correlation_id=correlation_id,
                idempotency_key=key,
            ),
        )

    if not ready:
        move(version, "build_failed", _key(version, "build_failed"))
        return
    for previous in KnowledgeVersionRepository(session, ctx).in_state(KnowledgeVersionState.READY):
        if previous.id != version.id:
            move(previous, "superseded", _key(version, "superseded", f":{previous.id}"))
    move(version, "build_ready", _key(version, "build_ready"))


def to_out(version: KnowledgeVersion) -> KnowledgeVersionOut:
    return KnowledgeVersionOut(
        id=version.id,
        project_id=version.project_id,
        version=version.version,
        status=version.state,
        cognee_dataset=version.cognee_dataset,
        master_snapshot_id=version.master_snapshot_id,
        okf_bundle_key=version.okf_bundle_key,
        okf_sha256=version.okf_sha256,
        coverage=KnowledgeCoverage.model_validate(version.coverage or {}),
        error_reason=version.error_reason,
        run_id=version.run_id,
    )


def sources_entry(row: KnowledgeSource) -> CoverageEntry:
    return CoverageEntry(
        document_version_id=row.document_version_id,
        status=SourceStatus(row.status),
        reason=row.reason,
    )


# --- POST P/knowledge/rebuild ---------------------------------------------------------------------


def _prior_rebuild(
    session: Session, user: CurrentUser, project_id: uuid.UUID, key: str
) -> Run | None:
    ctx = user.context(project_id)
    prior = find_by_key(session, ctx.tenant_id, "project", str(project_id), KEY_PREFIX + key)
    if prior is None or prior.action != REBUILD_STARTED or not prior.after:
        return None
    row = TemporalRunRepository(session, ctx).get(uuid.UUID(prior.after["run_id"]))
    return to_run(row) if row is not None else None


def _check_rebuildable(session: Session, user: CurrentUser, project_id: uuid.UUID) -> None:
    ctx = user.context(project_id)
    project = ProjectRepository(session, user.context()).get_for_update(project_id)
    if project is None:
        raise not_found()
    if project.status == ProjectStatus.ARCHIVED.value:
        raise project_archived()
    if P(project.state) in PIPELINE_BUILDING:
        raise KnowledgeBuilding()
    if KnowledgeVersionRepository(session, ctx).in_state(KnowledgeVersionState.BUILDING):
        raise KnowledgeBuilding()


def _commit_rebuild(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    run: Run,
    key: str | None,
    correlation_id: str,
) -> None:
    ctx = user.context(project_id)
    version = allocate_version(
        session,
        ctx,
        run_id=run.id,
        event="rebuild",
        actor=Actor.FDE,
        actor_id=str(user.user_id),
        correlation_id=correlation_id,
    )
    record(
        session,
        tenant_id=ctx.tenant_id,
        actor_id=str(user.user_id),
        action=REBUILD_STARTED,
        entity_type="project",
        entity_id=str(project_id),
        correlation_id=correlation_id,
        after={"run_id": str(run.id), "knowledge_version": version.version},
        idempotency_key=KEY_PREFIX + key if key else None,
    )
    session.commit()


async def start_rebuild(
    sessions: sessionmaker[Session],
    get_client: Callable[[], Awaitable[Client]],
    user: CurrentUser,
    project_id: uuid.UUID,
    *,
    idempotency_key: str | None = None,
    with_search_attributes: bool = True,
) -> Run:
    """Start `KnowledgeRebuild`; the new version is BUILDING when this returns."""
    ctx = user.context(project_id)
    correlation_id = correlation_id_var.get() or str(uuid7())
    with sessions() as session:
        try:
            if idempotency_key:
                replay = await asyncio.to_thread(
                    _prior_rebuild, session, user, project_id, idempotency_key
                )
                if replay is not None:
                    return replay
            await asyncio.to_thread(_check_rebuildable, session, user, project_id)
            try:
                client = await get_client()
            except OrchestratorUnavailable:
                raise TemporalUnavailable() from None
            spec = StartSpec(
                workflow=KNOWLEDGE_REBUILD,
                workflow_id=knowledge_workflow_id(project_id),
                queue=TaskQueue.KNOWLEDGE,
                subject_type=SubjectType.PROJECT,
                subject_id=project_id,
                stage="knowledge",
                input=RunInput(
                    tenant_id=ctx.tenant_id,
                    project_id=project_id,
                    run_id=uuid7(),
                    correlation_id=correlation_id,
                ),
            )
            try:
                run = await start_run(
                    client,
                    sessions,
                    spec,
                    with_search_attributes=with_search_attributes,
                    conflict=WorkflowIDConflictPolicy.FAIL,
                )
            except PipelineRunning:
                raise KnowledgeBuilding() from None
            except OrchestratorUnavailable:
                raise TemporalUnavailable() from None
            await asyncio.to_thread(
                _commit_rebuild, session, user, project_id, run, idempotency_key, correlation_id
            )
            return run
        except BaseException:
            await asyncio.to_thread(session.rollback)
            raise
