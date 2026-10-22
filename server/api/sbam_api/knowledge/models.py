"""`knowledge_versions` and `knowledge_sources` (TD §7, E5.2).

A knowledge version is one Cognee build of a project, numbered 1, 2, ... per project. At most one
is BUILDING at a time (partial unique index) and at most one READY: a new READY supersedes the
previous one (OUTDATED), and a FAILED build leaves the previous READY version usable.
`run_id` is the run that builds it, so an activity retry finds its own version instead of
allocating another. `knowledge_sources` lists the document versions a build used, one row each.
"""

import uuid

from sbam_contracts.knowledge import SourceStatus
from sbam_contracts.state_machines import KnowledgeVersionState, MachineName
from sqlalchemy import JSON, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check
from sbam_api.state_machine import Stateful, state_check
from sbam_api.state_machine.registry import register_entity

Json = JSON().with_variant(JSONB(), "postgresql")


class KnowledgeVersion(Stateful, ProjectOwned, Base):
    """`state` changes only through the state-machine service."""

    __tablename__ = "knowledge_versions"
    __table_args__ = (
        state_check(MachineName.KNOWLEDGE_VERSION),
        UniqueConstraint("project_id", "version"),
        UniqueConstraint("run_id"),
    )
    machine = MachineName.KNOWLEDGE_VERSION

    version: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    cognee_dataset: Mapped[str] = mapped_column(String(120))
    master_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.master_snapshots.id")
    )
    okf_bundle_key: Mapped[str | None] = mapped_column(String(255))  # E5.3
    okf_sha256: Mapped[str | None] = mapped_column(String(64))  # E5.3
    coverage: Mapped[dict[str, object]] = mapped_column(Json, default=dict)  # KnowledgeCoverage
    error_reason: Mapped[str | None] = mapped_column(String(300))  # a code, never document text


class KnowledgeSource(ProjectOwned, Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("knowledge_version_id", "document_version_id"),
        status_check(SourceStatus),
    )

    knowledge_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.knowledge_versions.id"), index=True
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.document_versions.id")
    )
    sha256: Mapped[str | None] = mapped_column(String(64))  # of the doc.md that was added
    status: Mapped[str] = mapped_column(String(20))  # SourceStatus
    reason: Mapped[str | None] = mapped_column(String(300))


# One BUILDING version per project: a second build (pipeline or rebuild) is refused by the DB too.
Index(
    "uq_knowledge_versions_one_building",
    KnowledgeVersion.project_id,
    unique=True,
    postgresql_where=KnowledgeVersion.state == KnowledgeVersionState.BUILDING.value,
    sqlite_where=KnowledgeVersion.state == KnowledgeVersionState.BUILDING.value,
)

register_entity(MachineName.KNOWLEDGE_VERSION, KnowledgeVersion)
