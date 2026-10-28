"""sub_skill_edits

Revision ID: 0032_sub_skill_edits
Revises: 0031_sub_skills
Create Date: 2026-10-28

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E8.4: the per-field edit history of a Sub-Skill.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_sub_skill_edits"
down_revision: str | Sequence[str] | None = "0031_sub_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "sub_skill_edits",
        sa.Column("sub_skill_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column("old", JSON, nullable=False),
        sa.Column("new", JSON, nullable=False),
        sa.Column("edited_by", sa.String(length=255), nullable=False),
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
            ["sub_skill_id"],
            ["sbam.sub_skills.id"],
            name=op.f("fk_sub_skill_edits_sub_skill_id_sub_skills"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["sbam.workflows.id"],
            name=op.f("fk_sub_skill_edits_workflow_id_workflows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sub_skill_edits")),
        schema="sbam",
    )
    for column in ("project_id", "sub_skill_id", "tenant_id", "workflow_id"):
        op.create_index(
            op.f(f"ix_sub_skill_edits_{column}"),
            "sub_skill_edits",
            [column],
            unique=False,
            schema="sbam",
        )


def downgrade() -> None:
    for column in ("workflow_id", "tenant_id", "sub_skill_id", "project_id"):
        op.drop_index(
            op.f(f"ix_sub_skill_edits_{column}"), table_name="sub_skill_edits", schema="sbam"
        )
    op.drop_table("sub_skill_edits", schema="sbam")
