"""`build_knowledge` (E5.2, TD §13.2-13.3): one knowledge version of a project in Cognee.

`build_version` is the whole step and takes its dependencies as arguments, so tests run it with
a fake Cognee and a temp-dir storage; the activity only builds the real ones. For run `inp`:

  1. allocate the run's knowledge version (BUILDING), or find it on a retry; a version that
     already finished is returned as it is;
  2. pin the masters snapshot (once) and export `masters.jsonl` (E3.6);
  3. take the project's document versions that are PARSED with a CLEAN scan and have a `doc.md`
     derivative; the rest are FAILED in the coverage with a reason;
  4. add each document on its own: one that Cognee refuses is FAILED, the others go on. Cognee
     or Bifrost down (`CogneeUnavailable`) fails the attempt, which Temporal retries;
  5. prune the project's dataset to this build's scope (#450): every item that is not one of
     these documents, this snapshot or its master documents is removed (a deleted or replaced
     document, an older snapshot), with the graph nodes only it sourced. Only once something
     was added: a build that fails before that never strips the graph the READY version uses;
  6. cognify (heartbeating from a separate task: it runs for minutes), then count the entities;
  7. save the coverage; the version stays BUILDING. `build_okf_bundle` (E5.3) makes it READY once
     its bundle validates. A retry after this point returns at once (no second cognify).
No document added, or no entity after cognify: FAILED, non-retryable `EmptyCorpus`. Cognee still
down on the last attempt: FAILED. A FAILED build leaves the previous READY version usable.
Payloads carry ids only; content is read from storage here. Logs carry ids and counts only.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

import sbam_api.knowledge.models  # noqa: F401 - registers KnowledgeVersion for the state machine
from sbam_api.core.auth import CurrentUser
from sbam_api.core.logging import correlation_id_var
from sbam_api.db.base import utcnow
from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import DocumentVersion
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.knowledge.repository import KnowledgeVersionRepository
from sbam_api.knowledge.service import (
    KnowledgeBuilding,
    allocate_version,
    finish_version,
    record_source,
)
from sbam_api.masters.service import create_snapshot, export_master_context, snapshot_documents
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.base import StorageBackend
from sbam_api.storage.factory import make_backend
from sbam_contracts.documents import ScanStatus
from sbam_contracts.knowledge import (
    CoverageEntry,
    ItemSource,
    KnowledgeCoverage,
    KnowledgeItem,
    ProjectScope,
    SourceStatus,
)
from sbam_contracts.pipeline import IdResult
from sbam_contracts.runs import RunInput, TaskQueue
from sbam_contracts.state_machines import Actor, KnowledgeVersionState
from sbam_contracts.state_machines import DocumentVersionState as D
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter, CogneeUnavailable, item_key
from sbam_workers.knowledge.derivatives import DOC_MD, Derivative, derivatives_of
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.knowledge.build")

BUILD_KNOWLEDGE_ATTEMPTS = 5  # TD §13.3; ProjectPipeline's PLAN uses the same (a test checks)
HEARTBEAT_SECONDS = 30.0  # the activity's heartbeat timeout is 2 minutes
EMPTY_CORPUS = "EmptyCorpus"
COGNEE_UNAVAILABLE = "CogneeUnavailable"
BUILDING_ELSEWHERE = "KnowledgeBuilding"
NOT_PARSED = "NOT_PARSED"  # not PARSED + CLEAN, or no doc.md derivative (E4.5)
SYSTEM_SUB = "system:knowledge"  # `created_by` of the snapshot this build pins


class EmptyCorpus(Exception):  # noqa: N818 - name from the E5.2 spec
    """Nothing usable reached the graph; retrying cannot help."""


@dataclass(frozen=True)
class BuildDeps:
    sessions: sessionmaker[Session]
    storage: StorageBackend
    adapter: CogneeAdapter
    heartbeat: Callable[[], None] = lambda: None
    derivatives: Callable[[Any], dict[str, Derivative]] = derivatives_of
    clock: Callable[[], datetime] = utcnow
    heartbeat_seconds: float = HEARTBEAT_SECONDS


@dataclass(frozen=True)
class _Version:
    id: uuid.UUID
    number: int
    state: str
    snapshot_id: uuid.UUID | None
    graph_built: bool  # coverage has entities: steps 1-6 are done


def _system_user(inp: RunInput) -> CurrentUser:
    """The masters service's caller for a knowledge build: the run's tenant, no roles."""
    return CurrentUser(
        user_id=uuid.UUID(int=0),
        keycloak_sub=SYSTEM_SUB,
        tenant_id=inp.tenant_id,
        roles=frozenset(),
    )


def _ctx(inp: RunInput) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _allocate(deps: BuildDeps, inp: RunInput) -> _Version:
    """The run's version. A rebuild's was allocated by the API (event `rebuild`); the pipeline's
    is allocated here."""
    with deps.sessions() as session:
        version = allocate_version(
            session,
            _ctx(inp),
            run_id=inp.run_id,
            event="build_started",
            actor=Actor.SYSTEM,
            actor_id=SYSTEM_ACTOR_ID,
            correlation_id=inp.correlation_id,
        )
        session.commit()
        built = KnowledgeCoverage.model_validate(version.coverage or {}).entities > 0
        return _Version(
            version.id, version.version, version.state, version.master_snapshot_id, built
        )


def _pin_masters(
    deps: BuildDeps, inp: RunInput, version: _Version
) -> tuple[KnowledgeItem, int, list[KnowledgeItem]]:
    """The version's snapshot (pinned once, kept on retries) as a MASTERS item, the number of
    master entries in it, and a MASTER_DOCUMENT item for each parsed master document it pinned."""
    user = _system_user(inp)
    with deps.sessions() as session:
        snapshot_id = version.snapshot_id
        if snapshot_id is None:
            snapshot_id = create_snapshot(session, user, inp.project_id).id
            row = KnowledgeVersionRepository(session, _ctx(inp)).get(version.id)
            if row is None:
                raise RuntimeError("knowledge version vanished")
            row.master_snapshot_id = snapshot_id
            session.commit()
        report = export_master_context(
            session, user, snapshot_id, inp.project_id, deps.storage, deps.clock()
        )
        pinned = snapshot_documents(session, user, snapshot_id)
    item = KnowledgeItem(
        source=ItemSource.MASTERS,
        source_id=snapshot_id,
        sha256=report.sha256,
        storage_key=report.storage_key,
    )
    documents = [
        KnowledgeItem(
            source=ItemSource.MASTER_DOCUMENT,
            source_id=doc.document_id,
            sha256=doc.derived_md_key.rsplit("/", 1)[-1],  # derivatives are named by their hash
            storage_key=doc.derived_md_key,
        )
        for doc in pinned
    ]
    return item, len(report.included), documents


def _documents(deps: BuildDeps, inp: RunInput) -> tuple[list[KnowledgeItem], list[CoverageEntry]]:
    """Items for the usable document versions; FAILED entries for the others."""
    items: list[KnowledgeItem] = []
    failed: list[CoverageEntry] = []
    with deps.sessions() as session:
        repo = DocumentVersionRepository(session, _ctx(inp))
        for version_id in repo.active_ids():
            version: DocumentVersion | None = repo.get(version_id)
            if version is None:
                continue
            md = deps.derivatives(version).get(DOC_MD)
            usable = version.state == D.PARSED.value and version.scan_status == ScanStatus.CLEAN
            if not usable or md is None:
                failed.append(
                    CoverageEntry(
                        document_version_id=version.id,
                        status=SourceStatus.FAILED,
                        reason=NOT_PARSED,
                    )
                )
                continue
            items.append(
                KnowledgeItem(
                    source=ItemSource.DOCUMENT,
                    source_id=version.id,
                    sha256=md.sha256,
                    storage_key=md.storage_key,
                )
            )
    return items, failed


def _record(
    deps: BuildDeps,
    inp: RunInput,
    version_id: uuid.UUID,
    entries: list[CoverageEntry],
    sha: dict[uuid.UUID, str],
) -> None:
    with deps.sessions() as session:
        row = KnowledgeVersionRepository(session, _ctx(inp)).get(version_id)
        if row is None:
            raise RuntimeError("knowledge version vanished")
        for entry in entries:
            record_source(session, _ctx(inp), row, entry, sha.get(entry.document_version_id))
        session.commit()


def _finish(
    deps: BuildDeps,
    inp: RunInput,
    version_id: uuid.UUID,
    coverage: KnowledgeCoverage,
    error: str | None,
) -> None:
    with deps.sessions() as session:
        row: KnowledgeVersion | None = KnowledgeVersionRepository(session, _ctx(inp)).get(
            version_id
        )
        if row is None:
            raise RuntimeError("knowledge version vanished")
        finish_version(
            session,
            _ctx(inp),
            row,
            ready=error is None,
            coverage=coverage,
            correlation_id=inp.correlation_id,
            error_reason=error,
        )
        session.commit()


def _save_coverage(
    deps: BuildDeps, inp: RunInput, version_id: uuid.UUID, coverage: KnowledgeCoverage
) -> None:
    with deps.sessions() as session:
        row = KnowledgeVersionRepository(session, _ctx(inp)).get(version_id)
        if row is None:
            raise RuntimeError("knowledge version vanished")
        row.coverage = coverage.model_dump(mode="json")
        session.commit()


async def _heartbeating[R](deps: BuildDeps, work: Awaitable[R]) -> R:
    """Run `work` while a separate task heartbeats, so a minutes-long cognify stays alive."""

    async def beat() -> None:
        while True:
            deps.heartbeat()
            await asyncio.sleep(deps.heartbeat_seconds)

    beater = asyncio.create_task(beat())
    try:
        return await work
    finally:
        beater.cancel()


async def build_version(deps: BuildDeps, inp: RunInput, *, attempt: int = 1) -> uuid.UUID:
    """The knowledge version id of run `inp`. See the module docstring for the steps."""
    correlation_id_var.set(inp.correlation_id)  # adapter log lines carry it (#351)
    try:
        version = await asyncio.to_thread(_allocate, deps, inp)
    except KnowledgeBuilding:  # another build of this project is running; try again later
        raise ApplicationError(
            "another knowledge build is running", type=BUILDING_ELSEWHERE
        ) from None
    if version.state != KnowledgeVersionState.BUILDING.value or version.graph_built:
        return version.id  # a retry after the version finished
    scope = ProjectScope(tenant_id=inp.tenant_id, project_id=inp.project_id)
    coverage = KnowledgeCoverage()
    try:
        masters, included, master_docs = await asyncio.to_thread(_pin_masters, deps, inp, version)
        items, entries = await asyncio.to_thread(_documents, deps, inp)
        sha = {item.source_id: item.sha256 for item in items}
        await deps.adapter.ensure_dataset(scope)
        for item in items:
            entries.append(await _add_one(deps.adapter, scope, item))
        if included and not await _add_masters(deps.adapter, scope, masters):
            included = 0
        added_docs = 0
        for master_doc in master_docs:  # context only: never an FDE source (C3)
            added_docs += await _add_masters(deps.adapter, scope, master_doc)
        coverage = KnowledgeCoverage(
            documents=_sorted(entries), masters_included=included, master_documents=added_docs
        )
        await asyncio.to_thread(_record, deps, inp, version.id, coverage.documents, sha)
        if not any(e.status is SourceStatus.ADDED for e in entries):
            raise EmptyCorpus("no document could be added")
        await deps.adapter.prune(scope, {item_key(i) for i in (*items, masters, *master_docs)})
        await _heartbeating(deps, deps.adapter.cognify(scope))
        page = await deps.adapter.graph_page(scope, None, 1)
        coverage = coverage.model_copy(update={"entities": page.total_nodes})
        if page.total_nodes == 0:
            raise EmptyCorpus("cognify produced no entities")
    except EmptyCorpus as exc:
        await asyncio.to_thread(_finish, deps, inp, version.id, coverage, EMPTY_CORPUS)
        raise ApplicationError(str(exc), type=EMPTY_CORPUS, non_retryable=True) from None
    except CogneeUnavailable as exc:
        if attempt >= BUILD_KNOWLEDGE_ATTEMPTS:  # Temporal will not retry: record the failure
            await asyncio.to_thread(_finish, deps, inp, version.id, coverage, COGNEE_UNAVAILABLE)
        raise ApplicationError(str(exc), type=COGNEE_UNAVAILABLE) from None
    await asyncio.to_thread(_save_coverage, deps, inp, version.id, coverage)
    log.info(
        "knowledge graph built",
        extra={
            "tenant_id": str(inp.tenant_id),
            "project_id": str(inp.project_id),
            "version": version.number,
            "entities": coverage.entities,
        },
    )
    return version.id


async def _add_one(
    adapter: CogneeAdapter, scope: ProjectScope, item: KnowledgeItem
) -> CoverageEntry:
    """One document; Cognee down propagates, anything else is this document's failure only."""
    try:
        await adapter.add(scope, [item])
    except CogneeUnavailable:
        raise
    except Exception as exc:  # one failing document must not stop the others
        return CoverageEntry(
            document_version_id=item.source_id,
            status=SourceStatus.FAILED,
            reason=type(exc).__name__,
        )
    return CoverageEntry(document_version_id=item.source_id, status=SourceStatus.ADDED)


async def _add_masters(adapter: CogneeAdapter, scope: ProjectScope, item: KnowledgeItem) -> bool:
    """The masters context (masters.jsonl, or a parsed master document) helps but is not
    required: a refusal is logged, the build goes on."""
    try:
        await adapter.add(scope, [item])
    except CogneeUnavailable:
        raise
    except Exception as exc:
        log.warning("masters context not added", extra={"exc_type": type(exc).__name__})
        return False
    return True


def _sorted(entries: list[CoverageEntry]) -> list[CoverageEntry]:
    return sorted(entries, key=lambda e: str(e.document_version_id))


@lru_cache(maxsize=1)
def _deps() -> BuildDeps:
    from sbam_workers.knowledge.cognee_backend import default_adapter

    return BuildDeps(
        sessions=mirror.sessions(),
        storage=make_backend(WorkerSettings()),  # type: ignore[call-arg]  # values from the env
        adapter=default_adapter(),
    )


def _with_heartbeat(deps: BuildDeps) -> BuildDeps:
    return BuildDeps(
        sessions=deps.sessions,
        storage=deps.storage,
        adapter=deps.adapter,
        heartbeat=activity.heartbeat,
        derivatives=deps.derivatives,
        clock=deps.clock,
        heartbeat_seconds=deps.heartbeat_seconds,
    )


@activity_for(TaskQueue.KNOWLEDGE)
@activity.defn(name="build_knowledge")
async def build_knowledge(inp: RunInput) -> IdResult:
    deps = _with_heartbeat(_deps())
    return IdResult(id=await build_version(deps, inp, attempt=activity.info().attempt))
