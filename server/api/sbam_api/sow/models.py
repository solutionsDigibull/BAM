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

from sbam_contracts.state_machines import MachineName
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check
from sbam_api.state_machine import Stateful, state_check
from sbam_api.state_machine.registry import register_entity

Json = JSON().with_variant(JSONB(), "postgresql")


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
    structured: Mapped[dict[str, Any] | None] = mapped_column(Json)  # SowDocument
    content_tiptap: Mapped[dict[str, Any] | None] = mapped_column(Json)  # E6.3 editor document
    sha256: Mapped[str | None] = mapped_column(String(64))  # of the canonical `structured` JSON
    error_reason: Mapped[str | None] = mapped_column(String(300))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(255))


register_entity(MachineName.SOW_VERSION, SowVersion)
