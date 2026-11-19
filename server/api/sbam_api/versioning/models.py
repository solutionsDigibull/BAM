"""`workflow_versions` (TD §7): one row per frozen Workflow version.

A row is the record that the version was frozen, so it is inserted already frozen: the shared
`forbid_frozen_change` trigger (migration 0062) makes any later UPDATE or DELETE an error (423 from
the repository). `(workflow_id, version)` is unique, so a retried freeze finds its row.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

import sbam_api.manifests.models  # noqa: F401 - workflow_manifests, a foreign-key target
from sbam_api.db.base import SCHEMA, Base, ProjectOwned
from sbam_api.manifests.models import _sqlite_frozen_guard


class WorkflowVersionRow(ProjectOwned, Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version"),
        CheckConstraint("is_frozen", name="always_frozen"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.workflows.id"), index=True)
    version: Mapped[str] = mapped_column(String(20))  # MAJOR.0.PATCH
    major: Mapped[int] = mapped_column(Integer)
    patch: Mapped[int] = mapped_column(Integer)
    sow_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    workflow_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workflow_manifests.id")
    )
    change_reason: Mapped[str] = mapped_column(String(500))
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


event.listen(WorkflowVersionRow.__table__, "after_create", _sqlite_frozen_guard)
