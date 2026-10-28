"""`workflow_schemas` (TD §7, E9.4): the versions of a Workflow's foundational `schema.sql`.

A version is either the FDE's uploaded base (`UPLOADED`, `source_document_id`, PENDING until a
build applies it) or what a build produced from it / from generated DDL, with the SCHEMA_SQL
verdict. The SQL itself lives in storage (`storage_key`, content-addressed `sha256`).
"""

import uuid
from typing import Any

from sbam_contracts.build_dashboard import GateStatus
from sbam_contracts.schema_sql import SchemaSource
from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

import sbam_api.ingest.models  # documents, a foreign-key target
import sbam_api.workflows.models  # noqa: F401 - workflows, a foreign-key target
from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check

Json = JSON().with_variant(JSONB(), "postgresql")


class WorkflowSchema(ProjectOwned, Base):
    __tablename__ = "workflow_schemas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workflow_id", "version"),
        status_check(SchemaSource, "source"),
        status_check(GateStatus),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.workflows.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.documents.id")
    )
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    sha256: Mapped[str | None] = mapped_column(String(64))
    tables: Mapped[list[str]] = mapped_column(Json, default=list)
    status: Mapped[str] = mapped_column(String(20))
    findings: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
