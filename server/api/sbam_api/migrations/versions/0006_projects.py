"""admin projects

Revision ID: 0006_projects
Revises: 0005_identity
Create Date: 2026-10-14

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_projects"
down_revision: str | Sequence[str] | None = "0005_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES = (
    "'IDLE', 'INGESTING', 'KNOWLEDGE_BUILDING', 'SOW_GENERATING', 'SOW_REVIEW', 'SOW_APPROVED', "
    "'WORKFLOW_CREATED', 'INGESTION_FAILED', 'KNOWLEDGE_FAILED', 'SOW_GENERATION_FAILED', "
    "'CANCELLED'"
)


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("client_name", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("context_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
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
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_projects_row_version_positive")),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')", name=op.f("ck_projects_status_valid")
        ),
        sa.CheckConstraint(f"state IN ({_STATES})", name=op.f("ck_projects_state_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        schema="sbam",
    )
    op.create_index(op.f("ix_projects_state"), "projects", ["state"], unique=False, schema="sbam")
    op.create_index(
        op.f("ix_projects_tenant_id"), "projects", ["tenant_id"], unique=False, schema="sbam"
    )
    op.create_index(
        "uq_projects_tenant_name_ci",
        "projects",
        ["tenant_id", sa.literal_column("lower(name)")],
        unique=True,
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index("uq_projects_tenant_name_ci", table_name="projects", schema="sbam")
    op.drop_index(op.f("ix_projects_tenant_id"), table_name="projects", schema="sbam")
    op.drop_index(op.f("ix_projects_state"), table_name="projects", schema="sbam")
    op.drop_table("projects", schema="sbam")
