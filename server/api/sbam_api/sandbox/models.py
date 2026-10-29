"""`sandbox_runs`: one row per sandbox run of a code attempt against a dataset (TD §7.7, E13.2).

`(code_attempt_id, dataset_id, run_no)` is the natural key. A run is STARTED when the worker claims
it and FINISHED when it has an outcome; FINISHED sets `is_frozen`, so the shared
`forbid_frozen_change` trigger makes the evidence immutable (a later UPDATE or DELETE is a 423).
The mode is stored, never a "certifying" flag that could disagree with it: whether a run may count
towards certification is `SandboxRun.certifying` in the contracts.

`dataset_id` carries no foreign key yet: `test_datasets` arrives with E13.4.
"""

import uuid
from datetime import datetime
from typing import Any

from sbam_contracts.sandbox import FailureCategory, InfraReason, SandboxMode, SandboxRunStatus
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import SCHEMA, Base, ProjectOwned, status_check

Json = JSON().with_variant(JSONB(), "postgresql")


class SandboxRunRecord(ProjectOwned, Base):
    __tablename__ = "sandbox_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code_attempt_id", "dataset_id", "run_no"),
        status_check(SandboxRunStatus),
        status_check(SandboxMode, "mode"),
        CheckConstraint(
            "failure_category IS NULL OR failure_category IN ("
            + ", ".join(f"'{c.value}'" for c in FailureCategory)
            + ")",
            name="failure_category_valid",
        ),
        CheckConstraint(
            "infra_reason IS NULL OR infra_reason IN ("
            + ", ".join(f"'{r.value}'" for r in InfraReason)
            + ")",
            name="infra_reason_valid",
        ),
        CheckConstraint("run_no >= 1", name="run_no_positive"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint("substr(runtime_digest, 1, 7) = 'sha256:'", name="runtime_digest_prefixed"),
        # FINISHED <=> frozen <=> finished_at set: a row cannot say two things at once.
        CheckConstraint(
            "(status = 'FINISHED' AND is_frozen AND finished_at IS NOT NULL)"
            " OR (status = 'STARTED' AND NOT is_frozen AND finished_at IS NULL)",
            name="finished_is_frozen",
        ),
        # infra_reason goes with INFRA_ERROR and with nothing else.
        CheckConstraint(
            "COALESCE(failure_category = 'INFRA_ERROR', false) = (infra_reason IS NOT NULL)",
            name="infra_reason_matches_category",
        ),
        # an unfinished run has no outcome yet
        CheckConstraint(
            "status = 'FINISHED' OR (failure_category IS NULL AND exit_code IS NULL)",
            name="outcome_only_when_finished",
        ),
    )

    code_attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey(f"{SCHEMA}.code_attempts.id"), index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    run_no: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    runtime_digest: Mapped[str] = mapped_column(String(71))
    vm_id: Mapped[str | None] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    failure_category: Mapped[str | None] = mapped_column(String(30))
    infra_reason: Mapped[str | None] = mapped_column(String(40))
    egress_log_key: Mapped[str | None] = mapped_column(String(1024))
    outputs_key: Mapped[str | None] = mapped_column(String(1024))
    logs_key: Mapped[str | None] = mapped_column(String(1024))
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # which Temporal activity execution owns the run, so a retry finds or supersedes it
    temporal_workflow_id: Mapped[str] = mapped_column(String(255))
    activity_id: Mapped[str] = mapped_column(String(255))
    activity_attempt: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any] | None] = mapped_column(Json)  # per-case outcome, no content
