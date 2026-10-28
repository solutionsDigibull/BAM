"""sow_impacts

Revision ID: 0033_sow_impacts
Revises: 0032_sub_skill_edits
Create Date: 2026-10-28

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E7.2: the scope-change impact stored when a new SOW version is approved.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_sow_impacts"
down_revision: str | Sequence[str] | None = "0032_sub_skill_edits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sow_impacts",
        sa.Column("sow_version_id", sa.Uuid(), nullable=False),
        sa.Column("base_version_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column(
            "report",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "affected_sub_skill_ids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
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
        sa.ForeignKeyConstraint(
            ["base_version_id"],
            ["sbam.sow_versions.id"],
            name=op.f("fk_sow_impacts_base_version_id_sow_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["sow_version_id"],
            ["sbam.sow_versions.id"],
            name=op.f("fk_sow_impacts_sow_version_id_sow_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sow_impacts")),
        sa.UniqueConstraint("sow_version_id", name=op.f("uq_sow_impacts_sow_version_id")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sow_impacts_project_id"),
        "sow_impacts",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sow_impacts_tenant_id"), "sow_impacts", ["tenant_id"], unique=False, schema="sbam"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sow_impacts_tenant_id"), table_name="sow_impacts", schema="sbam")
    op.drop_index(op.f("ix_sow_impacts_project_id"), table_name="sow_impacts", schema="sbam")
    op.drop_table("sow_impacts", schema="sbam")
