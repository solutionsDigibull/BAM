"""`certifications` (TD §7, E14.1): the certificate of one subject on one content hash.

A row is evidence that an automatic checklist passed, so it is inserted already frozen: the shared
`forbid_frozen_change` trigger makes any later UPDATE or DELETE a 423 (no revocation, a change is a
new version). `(level, subject_id, content_sha256)` is the natural key: a retried certify activity
finds its row instead of adding a second. `evidence` holds ids and hashes only
(`{code_attempt_id, bundle_sha256, gates[], sandbox_run_id, test_run_id, dataset_sha256,
suite_sha256}`), never code or outputs.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, ProjectOwned, status_check
from sbam_api.manifests.models import _sqlite_frozen_guard

Json = JSON().with_variant(JSONB(), "postgresql")


class CertificationLevel(StrEnum):
    SUB_SKILL = "SUB_SKILL"
    MACRO_SKILL = "MACRO_SKILL"
    WORKFLOW = "WORKFLOW"


class CertificationRow(ProjectOwned, Base):
    __tablename__ = "certifications"
    __table_args__ = (
        status_check(CertificationLevel, "level"),
        UniqueConstraint("tenant_id", "level", "subject_id", "content_sha256"),
        CheckConstraint("is_frozen", name="always_frozen"),
    )

    level: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    content_sha256: Mapped[str] = mapped_column(String(71))
    evidence: Mapped[dict[str, Any]] = mapped_column(Json)
    certified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


event.listen(CertificationRow.__table__, "after_create", _sqlite_frozen_guard)
