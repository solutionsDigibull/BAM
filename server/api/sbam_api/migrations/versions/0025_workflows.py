"""workflows

Revision ID: 0025_workflows
Revises: 0024_document_parse
Create Date: 2026-10-23

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E8.1: the Workflows SBAM creates from approved SOW versions (one per version, MAJOR.0.PATCH).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_workflows"
down_revision: str | Sequence[str] | None = "0024_document_parse"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("sow_version_id", sa.Uuid(), nullable=False),
        sa.Column("major", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
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
            "state IN ('CREATED', 'ISATVON_EXTRACTING', 'ISATVON_REVIEW', 'BUILDING', 'CERTIFYING',"
            " 'CERTIFIED', 'FROZEN', 'EXTRACTION_FAILED', 'NEEDS_FDE_REVIEW',"
            " 'WORKFLOW_VALIDATION_FAILED', 'CANCELLED')",
            name=op.f("ck_workflows_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["sow_version_id"],
            ["sbam.sow_versions.id"],
            name=op.f("fk_workflows_sow_version_id_sow_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflows")),
        sa.UniqueConstraint("project_id", "major", name=op.f("uq_workflows_project_id_major")),
        sa.UniqueConstraint(
            "project_id", "sow_version_id", name=op.f("uq_workflows_project_id_sow_version_id")
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_workflows_project_id"), "workflows", ["project_id"], unique=False, schema="sbam"
    )
    op.create_index(op.f("ix_workflows_state"), "workflows", ["state"], unique=False, schema="sbam")
    op.create_index(
        op.f("ix_workflows_tenant_id"), "workflows", ["tenant_id"], unique=False, schema="sbam"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workflows_tenant_id"), table_name="workflows", schema="sbam")
    op.drop_index(op.f("ix_workflows_state"), table_name="workflows", schema="sbam")
    op.drop_index(op.f("ix_workflows_project_id"), table_name="workflows", schema="sbam")
    op.drop_table("workflows", schema="sbam")
