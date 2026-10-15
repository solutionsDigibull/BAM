"""in-app notifications

Revision ID: 0009_notifications
Revises: 0008_rbac
Create Date: 2026-10-15

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_notifications"
down_revision: str | Sequence[str] | None = "0008_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPES = (
    "'SOW_READY', 'BREAKDOWN_READY', 'SUBSKILL_NEEDS_REVIEW', 'WORKFLOW_CERTIFIED', "
    "'WORKFLOW_FROZEN', 'EXPORT_READY', 'DOCUMENT_REJECTED', 'GENERATION_FAILED', "
    "'STAGE_COMPLETED', 'REMINDER'"
)


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.String(length=1000), nullable=False),
        sa.Column("link", sa.String(length=300), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temporal_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("activity_id", sa.String(length=255), nullable=False),
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
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_notifications_row_version_positive")),
        sa.CheckConstraint(f"type IN ({_TYPES})", name=op.f("ck_notifications_type_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint(
            "temporal_workflow_id",
            "activity_id",
            "user_id",
            name=op.f("uq_notifications_temporal_workflow_id_activity_id_user_id"),
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_notifications_tenant_id"), "notifications", ["tenant_id"], schema="sbam"
    )
    op.create_index(
        "ix_notifications_user_id_id", "notifications", ["user_id", "id"], schema="sbam"
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_id_id", table_name="notifications", schema="sbam")
    op.drop_index(op.f("ix_notifications_tenant_id"), table_name="notifications", schema="sbam")
    op.drop_table("notifications", schema="sbam")
