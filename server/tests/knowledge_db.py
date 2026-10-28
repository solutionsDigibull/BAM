"""SQLite world for the knowledge tests (E5.2, E5.3): a tenant, projects, document versions with
or without a parsed `doc.md`, masters tables and the knowledge tables. No Docker."""

import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

import sbam_api.ingest.models
import sbam_api.knowledge.models
import sbam_api.projects.models  # noqa: F401 - registers Project
from cognee_fake import FakeCognee
from sbam_api.audit.models import AuditLog, NotificationOutbox
from sbam_api.core.ids import uuid7
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.models import Tenant
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_api.knowledge.models import KnowledgeSource, KnowledgeVersion
from sbam_api.masters.models import MasterDocument, MasterEntry
from sbam_api.projects.models import Project
from sbam_api.storage.base import Kind, StorageBackend, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_api.temporal.models import TemporalRun
from sbam_contracts.knowledge import GraphEdge, GraphNode
from sbam_contracts.pipeline import StageRef
from sbam_contracts.runs import RunInput
from sbam_workers.knowledge.build import BuildDeps, build_version
from sbam_workers.knowledge.bundle import build_bundle
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter, Principal
from sbam_workers.knowledge.derivatives import DOC_JSON, DOC_MD, Derivative
from sbam_workers.knowledge.scope import SqlScopeGuard
from sqlalchemy import pool, select
from sqlalchemy.orm import Session, sessionmaker

TENANT = uuid7()
# Derivatives by document version id: what `document_versions.derivative_keys` will hold (#71).
DERIVED: dict[uuid.UUID, dict[str, Derivative]] = {}


def derived(version: Any) -> dict[str, Derivative]:
    """`BuildDeps.derivatives` for tests."""
    return DERIVED.get(version.id, {})


def knowledge_sessions() -> sessionmaker[Session]:
    engine = make_engine(
        "sqlite://", poolclass=pool.StaticPool, connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map={"sbam": None})
    t = Tenant.metadata.tables
    Tenant.metadata.create_all(
        engine,
        tables=[
            t["sbam.tenants"],
            t["sbam.users"],
            t["sbam.projects"],
            t["sbam.documents"],
            t["sbam.document_versions"],
            MasterEntry.metadata.tables["sbam.master_entries"],
            MasterEntry.metadata.tables["sbam.master_entry_versions"],
            MasterEntry.metadata.tables["sbam.master_snapshots"],
            MasterEntry.metadata.tables["sbam.master_documents"],
            KnowledgeVersion.__table__,  # type: ignore[list-item]
            KnowledgeSource.__table__,  # type: ignore[list-item]
            TemporalRun.__table__,  # type: ignore[list-item]
            AuditLog.__table__,  # type: ignore[list-item]
            NotificationOutbox.__table__,  # type: ignore[list-item]
        ],
    )
    return make_sessionmaker(engine)


def add_project(
    sessions: sessionmaker[Session],
    tenant: uuid.UUID = TENANT,
    state: str = "KNOWLEDGE_BUILDING",
    project_id: uuid.UUID | None = None,
) -> uuid.UUID:
    with sessions() as session:
        project = Project(
            id=project_id or uuid7(),
            tenant_id=tenant,
            name=f"p-{uuid.uuid4().hex[:8]}",
            state=state,
        )
        session.add(project)
        session.commit()
        return project.id


def add_master_document(
    sessions: sessionmaker[Session],
    storage: StorageBackend,
    text: str,
    *,
    tenant: uuid.UUID = TENANT,
    project: uuid.UUID | None = None,
    parse: str = "PARSED",
    scan: str | None = "CLEAN",
    title: str = "standard.pdf",
) -> uuid.UUID:
    """A master document (tenant-wide with no project) whose parsed `doc.md` is stored."""
    where = StorageScope(tenant, project or uuid.UUID(int=0))
    raw = storage.put(text.encode(), where, Kind.UPLOADS, "application/pdf")
    md = storage.put(text.encode(), where, Kind.DERIVED, "text/markdown")
    with sessions() as session:
        row = MasterDocument(
            tenant_id=tenant,
            project_id=project,
            title=title,
            filetype_row_label="Filetype #21",
            intent_class="Policy / rules",
            master_type="RULES",
            route_source="DEFAULT_BY_FILETYPE",
            storage_key=raw.storage_key,
            sha256=raw.sha256,
            size=raw.size,
            mime="application/pdf",
            scan_status=scan,
            parse_status=parse,
            derived_md_key=md.storage_key if parse == "PARSED" else None,
        )
        session.add(row)
        session.commit()
        return row.id


def add_document(
    sessions: sessionmaker[Session],
    storage: StorageBackend,
    project: uuid.UUID,
    text: str | None,
    *,
    tenant: uuid.UUID = TENANT,
    state: str = "PARSED",
    scan: str = "CLEAN",
    blocks: list[dict[str, Any]] | None = None,
) -> uuid.UUID:
    """A document version; with `text`, its parsed `doc.md` is stored and linked (E4.5's key)."""
    with sessions() as session:
        document = Document(id=uuid7(), tenant_id=tenant, project_id=project, title="doc.pdf")
        session.add(document)
        session.flush()
        version = DocumentVersion(
            id=uuid7(),
            tenant_id=tenant,
            project_id=project,
            document_id=document.id,
            storage_key=f"{tenant}/{project}/uploads/{hashlib.sha256(b'x').hexdigest()}",
            sha256=hashlib.sha256(b"x").hexdigest(),
            size=1,
            mime="application/pdf",
            state=state,
            scan_status=scan,
        )
        if text is not None:
            stored = storage.put(
                text.encode(), StorageScope(tenant, project), Kind.DERIVED, "text/markdown"
            )
            DERIVED[version.id] = {DOC_MD: Derivative(stored.storage_key, stored.sha256)}
            if blocks is not None:
                body = json.dumps({"blocks": blocks, "tables": []}).encode()
                where = StorageScope(tenant, project)
                doc_json = storage.put(body, where, Kind.DERIVED, "application/json")
                DERIVED[version.id][DOC_JSON] = Derivative(doc_json.storage_key, doc_json.sha256)
        session.add(version)
        session.commit()
        return version.id


def versions(sessions: sessionmaker[Session], project: uuid.UUID) -> list[KnowledgeVersion]:
    with sessions() as session:
        query = select(KnowledgeVersion).where(KnowledgeVersion.project_id == project)
        rows = list(session.scalars(query.order_by(KnowledgeVersion.version)))
        session.expunge_all()
        return rows


def sources(sessions: sessionmaker[Session], version: uuid.UUID) -> list[dict[str, Any]]:
    with sessions() as session:
        query = select(KnowledgeSource).where(KnowledgeSource.knowledge_version_id == version)
        return [
            {"document_version_id": r.document_version_id, "status": r.status, "reason": r.reason}
            for r in session.scalars(query)
        ]


class Cognee(FakeCognee):
    """FakeCognee plus the knobs these tests need."""

    def __init__(self) -> None:
        super().__init__()
        self.refuse: set[str] = set()  # item key prefixes (document version ids) to refuse
        self.entities = True

    async def add(self, who: Principal, dataset: str, name: str, data: bytes) -> None:
        if any(name.startswith(prefix) for prefix in self.refuse):
            raise ValueError("unsupported content")  # one document's own failure
        await super().add(who, dataset, name, data)

    async def graph(self, who: Principal, dataset: str) -> Any:
        """Cognee's shape (E5.1/#26): TextDocument (named after the item) <- DocumentChunk
        (`is_part_of`), chunk -> Entity (`contains`), Entity -> EntityType (`is_a`), and an
        `related_to` edge between consecutive entities. `entities=False`: an empty graph."""
        self._call("graph")
        if not self.entities:
            return [], []
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        concept = GraphNode(id="type-concept", type="EntityType", name="Concept")
        for name, data in sorted(self._ds(who, dataset).items()):
            doc = GraphNode(id=f"doc-{name}", type="TextDocument", name=name)
            chunk = GraphNode(id=f"chunk-{name}", type="DocumentChunk", name=name)
            nodes[doc.id], nodes[chunk.id], nodes[concept.id] = doc, chunk, concept
            edges.append(GraphEdge(source=chunk.id, target=doc.id, label="is_part_of"))
            words = re.findall(r"\b[A-Z][A-Za-z0-9-]+\b", data.decode("utf-8", "replace"))
            previous = None
            for word in dict.fromkeys(words):
                entity = GraphNode(id=f"ent-{word.lower()}", type="Entity", name=word)
                nodes[entity.id] = entity
                edges.append(GraphEdge(source=chunk.id, target=entity.id, label="contains"))
                edges.append(GraphEdge(source=entity.id, target=concept.id, label="is_a"))
                if previous is not None:
                    edges.append(GraphEdge(source=previous, target=entity.id, label="related_to"))
                previous = entity.id
        return list(nodes.values()), edges


class World:
    def __init__(self, root: Path) -> None:
        self.sessions: sessionmaker[Session] = knowledge_sessions()
        self.storage = LocalDiskBackend(root)
        self.cognee = Cognee()
        self.beats = 0
        adapter = CogneeAdapter(self.cognee, SqlScopeGuard(self.sessions), self.storage, "s" * 32)
        self.deps = BuildDeps(
            sessions=self.sessions,
            storage=self.storage,
            adapter=adapter,
            heartbeat=self.beat,
            derivatives=derived,
            heartbeat_seconds=0.001,
        )
        self.project = add_project(self.sessions)

    def beat(self) -> None:
        self.beats += 1

    def run_input(self, run_id: uuid.UUID | None = None) -> RunInput:
        return RunInput(
            tenant_id=TENANT,
            project_id=self.project,
            run_id=run_id or uuid7(),
            correlation_id=str(uuid.uuid4()),
        )

    def master_doc(self, text: str, **fields: Any) -> uuid.UUID:
        return add_master_document(self.sessions, self.storage, text, **fields)

    def docs(self, *texts: str | None, **fields: Any) -> list[uuid.UUID]:
        return [add_document(self.sessions, self.storage, self.project, t, **fields) for t in texts]

    def build(self, inp: RunInput, attempt: int = 1) -> uuid.UUID:
        return asyncio.run(build_version(self.deps, inp, attempt=attempt))

    def bundle(self, inp: RunInput, version: uuid.UUID) -> uuid.UUID:
        ref = StageRef(**inp.model_dump(), ref_id=version)
        return asyncio.run(build_bundle(self.deps, ref))

    def build_all(self, inp: RunInput) -> uuid.UUID:
        """The pipeline's knowledge + okf stages: graph, then bundle (READY)."""
        return self.bundle(inp, self.build(inp))
