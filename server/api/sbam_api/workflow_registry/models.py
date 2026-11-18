"""`registered_workflows` (ADR 0028, decision 4; #760): what SBAM has registered in Bifrost.

This table is the source of truth. Bifrost is where SRIM fetches from, and SBAM can rebuild every
Bifrost entry from here, so a lost Bifrost database is a recoverable fault. A row is inserted when
a certified, frozen Workflow is registered and changes once: REGISTERED becomes SUPERSEDED when a
later version replaces it. Rows are never deleted (no revocation, C-31).
"""

import uuid
from datetime import datetime
from typing import Any

from sbam_contracts.workflow_registry import RegistryStatus
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

import sbam_api.workflows.models  # noqa: F401 - workflows, a foreign-key target
from sbam_api.certifications.models import Json
from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check


class RegisteredWorkflowRow(ProjectOwned, Base):
    __tablename__ = "registered_workflows"
    __table_args__ = (
        status_check(RegistryStatus),
        UniqueConstraint(
            "tenant_id", "workflow_id", "version", name="uq_registered_workflows_version"
        ),
        UniqueConstraint("tenant_id", "registry_name", name="uq_registered_workflows_name"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.workflows.id"), index=True)
    version: Mapped[str] = mapped_column(String(20))  # MAJOR.0.PATCH
    registry_name: Mapped[str] = mapped_column(String(64))  # the Bifrost client name
    registry_id: Mapped[str] = mapped_column(String(160))  # Bifrost's id for the entry
    image_digest: Mapped[str] = mapped_column(String(71))  # as certified
    manifest_hash: Mapped[str] = mapped_column(String(71))  # the certified, frozen manifest
    certification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.certifications.id"), index=True
    )
    owner: Mapped[str] = mapped_column(String(160))
    tools: Mapped[list[dict[str, Any]]] = mapped_column(Json)  # McpToolEntry dumps, with schemas
    # The certified UI bundle entry (#857), copied from the frozen manifest; null on an entry
    # registered before the UI bundle existed.
    ui_bundle: Mapped[dict[str, Any] | None] = mapped_column(Json)
    status: Mapped[str] = mapped_column(String(20), default=RegistryStatus.REGISTERED.value)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[str | None] = mapped_column(String(20))  # the version that replaced it
