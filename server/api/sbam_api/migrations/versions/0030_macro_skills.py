"""macro_skills

Revision ID: 0030_macro_skills
Revises: 0029_filetype_row_versions
Create Date: 2026-10-28

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E8.2: the Macro Skills of ISATVON pass 1, and why a Workflow's extraction failed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_macro_skills"
down_revision: str | Sequence[str] | None = "0029_filetype_row_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "macro_skills",
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("sow_version_id", sa.Uuid(), nullable=False),
        sa.Column("macro_category", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column(
            "sow_refs",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("agent_ref", sa.String(length=80), nullable=True),
        sa.Column(
            "validation_errors",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
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
            "state IN ('PENDING', 'BUILDING', 'CERTIFIED', 'FROZEN')",
            name=op.f("ck_macro_skills_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["sow_version_id"],
            ["sbam.sow_versions.id"],
            name=op.f("fk_macro_skills_sow_version_id_sow_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["sbam.workflows.id"],
            name=op.f("fk_macro_skills_workflow_id_workflows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_macro_skills")),
        sa.UniqueConstraint(
            "workflow_id", "macro_category", name=op.f("uq_macro_skills_workflow_id_macro_category")
        ),
        sa.UniqueConstraint("workflow_id", "order", name=op.f("uq_macro_skills_workflow_id_order")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_macro_skills_project_id"),
        "macro_skills",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_macro_skills_state"), "macro_skills", ["state"], unique=False, schema="sbam"
    )
    op.create_index(
        op.f("ix_macro_skills_tenant_id"),
        "macro_skills",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_macro_skills_workflow_id"),
        "macro_skills",
        ["workflow_id"],
        unique=False,
        schema="sbam",
    )
    op.add_column(
        "workflows", sa.Column("error_reason", sa.String(length=300), nullable=True), schema="sbam"
    )


def downgrade() -> None:
    op.drop_column("workflows", "error_reason", schema="sbam")
    op.drop_index(op.f("ix_macro_skills_workflow_id"), table_name="macro_skills", schema="sbam")
    op.drop_index(op.f("ix_macro_skills_tenant_id"), table_name="macro_skills", schema="sbam")
    op.drop_index(op.f("ix_macro_skills_state"), table_name="macro_skills", schema="sbam")
    op.drop_index(op.f("ix_macro_skills_project_id"), table_name="macro_skills", schema="sbam")
    op.drop_table("macro_skills", schema="sbam")
