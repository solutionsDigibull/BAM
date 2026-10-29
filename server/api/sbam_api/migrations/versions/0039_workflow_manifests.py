"""workflow_manifests

Revision ID: 0039_workflow_manifests
Revises: 0038_gate_result_stage
Create Date: 2026-10-29

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E9.5: the Workflow manifest draft of each build (one row per workflow_id + build_no), with its
content hash; frozen rows can't change (the shared `forbid_frozen_change` trigger from 0001).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sbam_api.db.frozen import create_frozen_trigger, drop_frozen_trigger
from sqlalchemy.dialects import postgresql

revision: str = "0039_workflow_manifests"
down_revision: str | Sequence[str] | None = "0038_gate_result_stage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_manifests",
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("build_no", sa.Integer(), nullable=False),
        sa.Column(
            "manifest",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(length=71), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("is_frozen", sa.Boolean(), server_default="false", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["sbam.workflows.id"],
            name=op.f("fk_workflow_manifests_workflow_id_workflows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_manifests")),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_id",
            "build_no",
            name=op.f("uq_workflow_manifests_build"),
        ),
        schema="sbam",
    )
    for column in ("project_id", "tenant_id", "workflow_id"):
        op.create_index(
            op.f(f"ix_workflow_manifests_{column}"),
            "workflow_manifests",
            [column],
            unique=False,
            schema="sbam",
        )
    op.execute(create_frozen_trigger("workflow_manifests"))


def downgrade() -> None:
    op.execute(drop_frozen_trigger("workflow_manifests"))
    for column in ("workflow_id", "tenant_id", "project_id"):
        op.drop_index(
            op.f(f"ix_workflow_manifests_{column}"),
            table_name="workflow_manifests",
            schema="sbam",
        )
    op.drop_table("workflow_manifests", schema="sbam")
