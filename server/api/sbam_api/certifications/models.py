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
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check
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
        # 0050 created it under this name; the "uq" convention would spell out all four columns.
        UniqueConstraint(
            "tenant_id", "level", "subject_id", "content_sha256", name="uq_certifications_tenant_id"
        ),
        CheckConstraint("is_frozen", name="always_frozen"),
        # E14.3: a reused certificate names the one it carries over. The key it references is
        # (tenant, project, id), so a source in another tenant or project cannot be named, and the
        # database refuses it whatever the code does. NULL (every certificate before E14.3 and every
        # one earned by its own gates) is not checked.
        UniqueConstraint(
            "tenant_id", "project_id", "id", name="uq_certifications_tenant_project_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "reused_from_id"],
            [
                f"{SCHEMA}.certifications.tenant_id",
                f"{SCHEMA}.certifications.project_id",
                f"{SCHEMA}.certifications.id",
            ],
            name="fk_certifications_reused_from_id_certifications",
        ),
        CheckConstraint(
            "reused_from_id IS NULL OR reused_from_id <> id", name="reused_from_is_another"
        ),
    )

    level: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    content_sha256: Mapped[str] = mapped_column(String(71))
    evidence: Mapped[dict[str, Any]] = mapped_column(Json)
    certified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # E14.3: the certificate an unchanged Sub-Skill carried over instead of running the gates again.
    reused_from_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)


event.listen(CertificationRow.__table__, "after_create", _sqlite_frozen_guard)
