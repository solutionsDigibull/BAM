"""temporal_runs: mirror of Temporal executions (E1.7)

Revision ID: 0002_temporal_runs
Revises: 0001_baseline
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_temporal_runs"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = "'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'TERMINATED', 'TIMED_OUT'"
SUBJECTS = "'project', 'document_version', 'sub_skill'"


def upgrade() -> None:
    op.create_table(
        "temporal_runs",
        sa.Column("temporal_workflow_id", sa.String(length=200), nullable=False),
        sa.Column("temporal_run_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("task_queue", sa.String(length=32), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("current_stage", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"state IN ({STATES})", name=op.f("ck_temporal_runs_state_valid")),
        sa.CheckConstraint(
            f"subject_type IN ({SUBJECTS})", name=op.f("ck_temporal_runs_subject_type_valid")
        ),
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_temporal_runs_row_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_temporal_runs")),
        sa.UniqueConstraint(
            "temporal_workflow_id",
            "temporal_run_id",
            name=op.f("uq_temporal_runs_temporal_workflow_id_temporal_run_id"),
        ),
        schema="sbam",
    )
    for column in ("tenant_id", "project_id", "temporal_workflow_id", "state"):
        op.create_index(
            op.f(f"ix_temporal_runs_{column}"), "temporal_runs", [column], schema="sbam"
        )


def downgrade() -> None:
    for column in ("state", "temporal_workflow_id", "project_id", "tenant_id"):
        op.drop_index(op.f(f"ix_temporal_runs_{column}"), table_name="temporal_runs", schema="sbam")
    op.drop_table("temporal_runs", schema="sbam")
