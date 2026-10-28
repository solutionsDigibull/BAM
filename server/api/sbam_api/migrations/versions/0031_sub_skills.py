"""sub_skills

Revision ID: 0031_sub_skills
Revises: 0030_macro_skills
Create Date: 2026-10-28

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E8.3: the Sub-Skills of ISATVON pass 2, with their TD §10.4 findings.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_sub_skills"
down_revision: str | Sequence[str] | None = "0030_macro_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sub_skills",
        sa.Column("macro_skill_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.String(length=120), nullable=False),
        sa.Column("skill_name", sa.String(length=200), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column(
            "isatvon",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("isatvon_framework_version", sa.String(length=10), nullable=False),
        sa.Column(
            "sow_refs",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "validation_errors",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
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
            "state IN ('DRAFT', 'VALID', 'INVALID', 'BUILDING', 'CORRECTING', 'TESTING',"
            " 'TESTING_BLOCKED', 'NEEDS_FDE_REVIEW', 'DEPENDENCY_FAILED', 'BUILD_ERROR',"
            " 'CERTIFIED', 'FROZEN', 'INVALIDATED', 'SUPERSEDED')",
            name=op.f("ck_sub_skills_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["macro_skill_id"],
            ["sbam.macro_skills.id"],
            name=op.f("fk_sub_skills_macro_skill_id_macro_skills"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["sbam.workflows.id"],
            name=op.f("fk_sub_skills_workflow_id_workflows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sub_skills")),
        sa.UniqueConstraint(
            "macro_skill_id", "order", name=op.f("uq_sub_skills_macro_skill_id_order")
        ),
        sa.UniqueConstraint(
            "workflow_id", "skill_id", name=op.f("uq_sub_skills_workflow_id_skill_id")
        ),
        schema="sbam",
    )
    for column in ("macro_skill_id", "project_id", "state", "tenant_id", "workflow_id"):
        op.create_index(
            op.f(f"ix_sub_skills_{column}"), "sub_skills", [column], unique=False, schema="sbam"
        )


def downgrade() -> None:
    for column in ("workflow_id", "tenant_id", "state", "project_id", "macro_skill_id"):
        op.drop_index(op.f(f"ix_sub_skills_{column}"), table_name="sub_skills", schema="sbam")
    op.drop_table("sub_skills", schema="sbam")
