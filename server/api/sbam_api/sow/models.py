"""`sows` and `sow_versions` (TD §7.5, E6.2).

A project has one `sows` row, which points at its current version and at its approved version. A
version is immutable once written: an edit or a regeneration writes a new version whose
`parent_version_id` is the one it came from. `structured` is the validated `SowDocument` (null
when generation failed, the reason is then in `error_reason` as a code, never document text).
`(run_id, knowledge_version_id)` is unique, so a retried generation activity finds the version it
already wrote instead of writing a second one.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sbam_contracts.sow import SECTIONS
from sbam_contracts.state_machines import MachineName
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine

import sbam_api.knowledge.models
import sbam_api.llm.models  # noqa: F401 - llm_calls, a foreign-key target
from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check
from sbam_api.state_machine import Stateful, state_check
from sbam_api.state_machine.registry import register_entity

Json = JSON().with_variant(JSONB(), "postgresql")


class SowJson(TypeDecorator[dict[str, Any]]):
    """`Json` for a stored `SowDocument`: its sections come back in TD §10.6 order.

    Postgres JSONB keeps no key order (it sorts by length, then bytes), but `SowDocument` rejects
    sections that are out of order, so a document read back from JSONB could not be validated."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        return dialect.type_descriptor(JSONB() if dialect.name == "postgresql" else JSON())

    def process_result_value(self, value: dict[str, Any] | None, dialect: Dialect) -> Any:
        if isinstance(value, dict) and set(value) == set(SECTIONS):
            return {section: value[section] for section in SECTIONS}
        return value  # a failed version (null) or something that is not a whole SOW: as stored


class SowSource(StrEnum):
    GENERATED = "GENERATED"  # written by sbam-sow
    EDITED = "EDITED"  # saved by the FDE in the editor (E6.3)


class Sow(ProjectOwned, Base):
    __tablename__ = "sows"
    __table_args__ = (UniqueConstraint("project_id"),)

    # No foreign keys: sow_versions already points here, and the pair would be a cycle.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class SowVersion(Stateful, ProjectOwned, Base):
    """`state` changes only through the state-machine service."""

    __tablename__ = "sow_versions"
    __table_args__ = (
        state_check(MachineName.SOW_VERSION),
        status_check(SowSource, column="source"),
        UniqueConstraint("sow_id", "version"),
        UniqueConstraint("run_id", "knowledge_version_id"),
    )
    machine = MachineName.SOW_VERSION

    sow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.sows.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))  # SowSource
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    knowledge_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.knowledge_versions.id")
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.sow_versions.id")
    )
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.llm_calls.id"))
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    structured: Mapped[dict[str, Any] | None] = mapped_column(SowJson)  # SowDocument
    content_tiptap: Mapped[dict[str, Any] | None] = mapped_column(Json)  # E6.3 editor document
    sha256: Mapped[str | None] = mapped_column(String(64))  # of the canonical `structured` JSON
    error_reason: Mapped[str | None] = mapped_column(String(300))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    # When the pipeline took the approval (the signal, or the run that replaced it). NULL on an
    # approved version = the approval is saved and the pipeline has not been told yet (A-03).
    signal_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who is telling the pipeline right now (the approver's request, a retry or the relay): one at a
    # time, so a second approval never sends a second signal. A claim older than the stale window
    # belongs to a process that died.
    signal_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


register_entity(MachineName.SOW_VERSION, SowVersion)


class SowExport(ProjectOwned, Base):
    """A rendered export (E6.4), cached by what the file shows: the version (its content is
    immutable, `sow_sha256`), its status on the cover, the format and the template version. The same
    file is never rendered twice; an approval or supersession renders a new cover."""

    __tablename__ = "sow_exports"
    __table_args__ = (UniqueConstraint("sow_version_id", "status", "format", "template_version"),)

    sow_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.sow_versions.id"), index=True
    )
    sow_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))  # the version's state when rendered
    format: Mapped[str] = mapped_column(String(8))  # ExportFormat
    template_version: Mapped[str] = mapped_column(String(20))
    storage_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))  # of the file
    size: Mapped[int] = mapped_column(Integer)


class SowImpactRecord(ProjectOwned, Base):
    """`sow_impacts` (E7.2): the scope-change impact as it stood when a new version was approved.

    `report` is the `SowImpact` the FDE could preview (same function, same inputs), and
    `affected_sub_skill_ids` the Sub-Skills it invalidates in the Workflow of `base_version_id`.
    One per approved version; never updated. `workflow_id` has no foreign key: `workflows` refers
    to `sow_versions`, and the pair would be a cycle.
    """

    __tablename__ = "sow_impacts"
    __table_args__ = (UniqueConstraint("sow_version_id"),)

    sow_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.sow_versions.id"))
    base_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.sow_versions.id"))
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    report: Mapped[dict[str, Any]] = mapped_column(Json)  # SowImpact
    affected_sub_skill_ids: Mapped[list[str]] = mapped_column(Json)
