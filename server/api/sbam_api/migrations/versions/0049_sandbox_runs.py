"""sandbox_runs

Revision ID: 0049_sandbox_runs
Revises: 0048_rate_windows
Create Date: 2026-10-29

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E13.2: one row per sandbox run of a code attempt against a dataset. FINISHED sets `is_frozen`, so
the shared `forbid_frozen_change` trigger (0001) makes finished evidence immutable.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sbam_api.db.frozen import create_frozen_trigger, drop_frozen_trigger
from sqlalchemy.dialects import postgresql

revision: str = "0049_sandbox_runs"
down_revision: str | Sequence[str] | None = "0048_rate_windows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sandbox_runs",
        sa.Column("code_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("run_no", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("runtime_digest", sa.String(length=71), nullable=False),
        sa.Column("vm_id", sa.String(length=128), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("failure_category", sa.String(length=30), nullable=True),
        sa.Column("infra_reason", sa.String(length=40), nullable=True),
        sa.Column("egress_log_key", sa.String(length=1024), nullable=True),
        sa.Column("outputs_key", sa.String(length=1024), nullable=True),
        sa.Column("logs_key", sa.String(length=1024), nullable=True),
        sa.Column("truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_frozen", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("temporal_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("activity_id", sa.String(length=255), nullable=False),
        sa.Column("activity_attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "summary",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "COALESCE(failure_category = 'INFRA_ERROR', false) = (infra_reason IS NOT NULL)",
            name=op.f("ck_sandbox_runs_infra_reason_matches_category"),
        ),
        sa.CheckConstraint(
            "(status = 'FINISHED' AND is_frozen AND finished_at IS NOT NULL)"
            " OR (status = 'STARTED' AND NOT is_frozen AND finished_at IS NULL)",
            name=op.f("ck_sandbox_runs_finished_is_frozen"),
        ),
        sa.CheckConstraint(
            "failure_category IS NULL OR failure_category IN ('IMPORT_ERROR', 'RUNTIME_ERROR',"
            " 'TIMEOUT', 'OOM', 'OUTPUT_SCHEMA_MISMATCH', 'ASSERTION_FAILED', 'EGRESS_BLOCKED',"
            " 'INFRA_ERROR')",
            name=op.f("ck_sandbox_runs_failure_category_valid"),
        ),
        sa.CheckConstraint(
            "infra_reason IS NULL OR infra_reason IN ('HOST_UNREACHABLE', 'HASH_MISMATCH',"
            " 'TEMPLATE_DIGEST_MISMATCH', 'VM_CREATE_FAILED', 'VM_BOOT_TIMEOUT', 'RUNNER_FAILED',"
            " 'DATASET_INVALID', 'EGRESS_LOG_UNAVAILABLE', 'DESTROY_FAILED', 'CANCELLED',"
            " 'SUPERSEDED')",
            name=op.f("ck_sandbox_runs_infra_reason_valid"),
        ),
        sa.CheckConstraint(
            "mode IN ('CUBESANDBOX', 'LOCAL_DOCKER_NONCERT')",
            name=op.f("ck_sandbox_runs_mode_valid"),
        ),
        sa.CheckConstraint(
            "substr(runtime_digest, 1, 7) = 'sha256:'",
            name=op.f("ck_sandbox_runs_runtime_digest_prefixed"),
        ),
        sa.CheckConstraint(
            "status = 'FINISHED' OR (failure_category IS NULL AND exit_code IS NULL)",
            name=op.f("ck_sandbox_runs_outcome_only_when_finished"),
        ),
        sa.CheckConstraint(
            "status IN ('STARTED', 'FINISHED')", name=op.f("ck_sandbox_runs_status_valid")
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_sandbox_runs_duration_non_negative"),
        ),
        sa.CheckConstraint("run_no >= 1", name=op.f("ck_sandbox_runs_run_no_positive")),
        sa.ForeignKeyConstraint(
            ["code_attempt_id"],
            ["sbam.code_attempts.id"],
            name=op.f("fk_sandbox_runs_code_attempt_id_code_attempts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sandbox_runs")),
        sa.UniqueConstraint(
            "tenant_id",
            "code_attempt_id",
            "dataset_id",
            "run_no",
            name=op.f("uq_sandbox_runs_tenant_id_code_attempt_id_dataset_id_run_no"),
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sandbox_runs_code_attempt_id"),
        "sandbox_runs",
        ["code_attempt_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sandbox_runs_project_id"),
        "sandbox_runs",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sandbox_runs_tenant_id"),
        "sandbox_runs",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    op.execute(create_frozen_trigger("sandbox_runs"))


def downgrade() -> None:
    op.execute(drop_frozen_trigger("sandbox_runs"))
    op.drop_index(op.f("ix_sandbox_runs_tenant_id"), table_name="sandbox_runs", schema="sbam")
    op.drop_index(op.f("ix_sandbox_runs_project_id"), table_name="sandbox_runs", schema="sbam")
    op.drop_index(op.f("ix_sandbox_runs_code_attempt_id"), table_name="sandbox_runs", schema="sbam")
    op.drop_table("sandbox_runs", schema="sbam")
