"""`build_okf_bundle` (E5.3, TD §5.3): the OKF bundle of a knowledge version, then READY.

`build_bundle` is the whole step (tests pass a fake Cognee and temp storage); the activity builds
the real dependencies. For the BUILDING version `inp.ref_id`:
  1. its documents: the `knowledge_sources` rows (E5.2); for each ADDED one, doc.md (+ doc.json)
     read from storage with the hash checked; masters.jsonl re-exported from its pinned snapshot
     (same snapshot, same bytes);
  2. the whole graph of the project's dataset, page by page;
  3. the five parts (okf.build_parts) and bundle.json, validated before anything is written;
  4. the parts written content-addressed (`okf/<sha256>`): write-once, and a retry writes the
     same keys; the version gets `okf_bundle_key` / `okf_sha256` and becomes READY (the previous
     READY version OUTDATED).
Invalid (dangling relation, text not preserved, no covered document, ...): FAILED, non-retryable
`BundleInvalid`. A version that is already READY is returned as it is.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache

from sbam_api.db.repository import TenantContext
from sbam_api.ingest.models import DocumentVersion
from sbam_api.ingest.repository import DocumentVersionRepository
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.knowledge.repository import KnowledgeSourceRepository, KnowledgeVersionRepository
from sbam_api.knowledge.service import finish_version, sources_entry
from sbam_api.masters.service import export_master_context
from sbam_api.storage.base import Kind, StorageScope
from sbam_contracts.knowledge import (
    CoverageEntry,
    GraphEdge,
    GraphNode,
    KnowledgeCoverage,
    ProjectScope,
    SourceStatus,
)
from sbam_contracts.okf import BUNDLE, PARTS, PartRef
from sbam_contracts.pipeline import IdResult, StageRef
from sbam_contracts.runs import RunInput, TaskQueue
from sbam_contracts.state_machines import KnowledgeVersionState
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers.knowledge import okf
from sbam_workers.knowledge.build import NOT_PARSED, BuildDeps, _system_user
from sbam_workers.knowledge.derivatives import DOC_JSON, DOC_MD
from sbam_workers.registry import activity_for

log = logging.getLogger("sbam_workers.knowledge.bundle")

BUNDLE_INVALID = "BundleInvalid"
KNOWLEDGE_FAILED = "KnowledgeFailed"
GRAPH_PAGE = 500


@dataclass(frozen=True)
class _Inputs:
    number: int
    state: str
    snapshot_id: uuid.UUID | None
    docs: list[okf.Doc]
    outcomes: list[CoverageEntry]
    masters: bytes
    coverage: KnowledgeCoverage


def _ctx(inp: StageRef) -> TenantContext:
    return TenantContext(inp.tenant_id, inp.project_id)


def _version(session: Session, inp: StageRef) -> KnowledgeVersion:
    row: KnowledgeVersion | None = KnowledgeVersionRepository(session, _ctx(inp)).get(inp.ref_id)
    if row is None:
        raise ApplicationError("knowledge version not found", type="NotFound", non_retryable=True)
    return row


def _document(deps: BuildDeps, version: DocumentVersion) -> okf.Doc | None:
    found = deps.derivatives(version)
    md = found.get(DOC_MD)
    if md is None:
        return None
    text = deps.storage.get(md.storage_key, verify=True)  # content-addressed: hash checked
    doc_json = found.get(DOC_JSON)
    blocks = okf.parse_blocks(deps.storage.get(doc_json.storage_key) if doc_json else None)
    return okf.Doc(version.id, text.decode("utf-8"), md.sha256, blocks)


def _load(deps: BuildDeps, inp: StageRef) -> _Inputs:
    with deps.sessions() as session:
        row = _version(session, inp)
        coverage = KnowledgeCoverage.model_validate(row.coverage or {})
        if row.state != KnowledgeVersionState.BUILDING.value:
            return _Inputs(row.version, row.state, row.master_snapshot_id, [], [], b"", coverage)
        sources = KnowledgeSourceRepository(session, _ctx(inp)).of_version(row.id)
        versions = DocumentVersionRepository(session, _ctx(inp))
        docs: list[okf.Doc] = []
        outcomes: list[CoverageEntry] = []
        for source in sources:
            entry = sources_entry(source)
            document = versions.get(source.document_version_id)
            if entry.status is SourceStatus.ADDED and document is not None:
                doc = _document(deps, document)
                if doc is None:
                    entry = entry.model_copy(
                        update={"status": SourceStatus.FAILED, "reason": NOT_PARSED}
                    )
                else:
                    docs.append(doc)
            outcomes.append(entry)
        masters = b""
        if row.master_snapshot_id is not None:
            user = _system_user(RunInput(**inp.model_dump(exclude={"ref_id"})))
            report = export_master_context(
                session, user, row.master_snapshot_id, inp.project_id, deps.storage, deps.clock()
            )
            masters = deps.storage.get(report.storage_key, verify=True)
        return _Inputs(
            row.version, row.state, row.master_snapshot_id, docs, outcomes, masters, coverage
        )


async def _graph(deps: BuildDeps, scope: ProjectScope) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    cursor: str | None = None
    while True:
        page = await deps.adapter.graph_page(scope, cursor, GRAPH_PAGE)
        nodes += page.nodes
        edges += page.edges
        deps.heartbeat()
        if page.next_cursor is None:
            return nodes, edges
        cursor = page.next_cursor


def _finish(
    deps: BuildDeps,
    inp: StageRef,
    coverage: KnowledgeCoverage,
    *,
    bundle: PartRef | None,
    error: str | None,
) -> None:
    with deps.sessions() as session:
        row = _version(session, inp)
        if bundle is not None:
            row.okf_bundle_key, row.okf_sha256 = bundle.storage_key, bundle.sha256
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


async def build_bundle(deps: BuildDeps, inp: StageRef) -> uuid.UUID:
    loaded = await asyncio.to_thread(_load, deps, inp)
    if loaded.state == KnowledgeVersionState.READY.value:
        return inp.ref_id
    if loaded.state != KnowledgeVersionState.BUILDING.value:
        raise ApplicationError(
            "the knowledge build failed", type=KNOWLEDGE_FAILED, non_retryable=True
        )
    scope = ProjectScope(tenant_id=inp.tenant_id, project_id=inp.project_id)
    nodes, edges = await _graph(deps, scope)
    built = okf.build_parts(loaded.docs, loaded.outcomes, nodes, edges, loaded.masters)
    where = StorageScope(inp.tenant_id, inp.project_id)
    refs = {
        name: PartRef(storage_key=where.key(Kind.OKF, okf.sha256(data)), sha256=okf.sha256(data))
        for name, data in built.parts.items()
    }
    bundle = okf.manifest(
        version_id=inp.ref_id,
        project_id=inp.project_id,
        number=loaded.number,
        docs=loaded.docs,
        parts=refs,
        snapshot_id=loaded.snapshot_id,
        masters_sha256=okf.sha256(loaded.masters),
    )
    expected = {o.document_version_id for o in loaded.outcomes}
    problems = okf.validate(bundle, built.parts, loaded.docs, expected)
    coverage = loaded.coverage.model_copy(
        update={
            "bundle": built.coverage,
            "relations": built.counts["relations"],
            "chunks": built.counts["chunks"],
        }
    )
    if problems:
        await asyncio.to_thread(_finish, deps, inp, coverage, bundle=None, error=BUNDLE_INVALID)
        log.warning(
            "OKF bundle invalid",
            extra={"project_id": str(inp.project_id), "problems": problems[:20]},
        )
        raise ApplicationError("; ".join(problems[:20]), type=BUNDLE_INVALID, non_retryable=True)
    written = await asyncio.to_thread(_write, deps, inp, built.parts, bundle)
    await asyncio.to_thread(_finish, deps, inp, coverage, bundle=written, error=None)
    log.info(
        "OKF bundle ready",
        extra={"project_id": str(inp.project_id), "version": loaded.number, **built.counts},
    )
    return inp.ref_id


def _write(deps: BuildDeps, inp: StageRef, parts: dict[str, bytes], bundle: bytes) -> PartRef:
    """Content-addressed, so a retry writes the same keys and nothing is ever overwritten."""
    where = StorageScope(inp.tenant_id, inp.project_id)
    for name in PARTS:
        deps.storage.put(parts[name], where, Kind.OKF, _content_type(name))
    stored = deps.storage.put(bundle, where, Kind.OKF, _content_type(BUNDLE))
    return PartRef(storage_key=stored.storage_key, sha256=stored.sha256)


def _content_type(name: str) -> str:
    return "application/x-ndjson" if name.endswith(".jsonl") else "application/json"


@lru_cache(maxsize=1)
def _deps() -> BuildDeps:
    from sbam_workers.knowledge.build import _deps as build_deps

    return build_deps()


@activity_for(TaskQueue.KNOWLEDGE)
@activity.defn(name="build_okf_bundle")
async def build_okf_bundle(inp: StageRef) -> IdResult:
    """Returns the knowledge version id (the bundle's id)."""
    base = _deps()
    deps = BuildDeps(
        sessions=base.sessions,
        storage=base.storage,
        adapter=base.adapter,
        heartbeat=activity.heartbeat,
        derivatives=base.derivatives,
        clock=base.clock,
    )
    return IdResult(id=await build_bundle(deps, inp))
