"""users status and project members (RBAC)

Revision ID: 0008_rbac
Revises: 0007_documents
Create Date: 2026-10-14

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_rbac"
down_revision: str | Sequence[str] | None = "0007_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows are users who already signed in: ACTIVE.
    op.add_column(
        "users",
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        schema="sbam",
    )
    op.create_check_constraint(
        op.f("ck_users_status_valid"),
        "users",
        "status IN ('INVITED', 'ACTIVE', 'DEACTIVATED')",
        schema="sbam",
    )
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
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
            "row_version >= 1", name=op.f("ck_project_members_row_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["sbam.projects.id"],
            name=op.f("fk_project_members_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["sbam.users.id"],
            name=op.f("fk_project_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_members")),
        sa.UniqueConstraint(
            "project_id", "user_id", name=op.f("uq_project_members_project_id_user_id")
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_project_members_tenant_id"),
        "project_members",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_project_members_user_id"),
        "project_members",
        ["user_id"],
        unique=False,
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_members_user_id"), table_name="project_members", schema="sbam")
    op.drop_index(op.f("ix_project_members_tenant_id"), table_name="project_members", schema="sbam")
    op.drop_table("project_members", schema="sbam")
    op.drop_constraint(op.f("ck_users_status_valid"), "users", schema="sbam", type_="check")
    op.drop_column("users", "status", schema="sbam")
