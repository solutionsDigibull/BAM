"""master_lists: the tenant's template lists (#782)

Revision ID: 0065_master_lists
Revises: 0064_export_artifacts
Create Date: 2026-11-19

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

Employees, Customers, Assets and Systems: one row per (tenant, list), headers and rows as JSON,
replaced as a whole by `PUT /admin/master-lists/{key}`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065_master_lists"
down_revision: str | Sequence[str] | None = "0064_export_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEYS = "'employees', 'customers', 'assets', 'systems'"


def upgrade() -> None:
    op.create_table(
        "master_lists",
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_master_lists_row_version_positive")),
        sa.CheckConstraint(f"key IN ({_KEYS})", name=op.f("ck_master_lists_key_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_lists")),
        sa.UniqueConstraint("tenant_id", "key", name=op.f("uq_master_lists_tenant_id_key")),
        schema="sbam",
    )
    op.create_index(op.f("ix_master_lists_tenant_id"), "master_lists", ["tenant_id"], schema="sbam")


def downgrade() -> None:
    op.drop_index(op.f("ix_master_lists_tenant_id"), table_name="master_lists", schema="sbam")
    op.drop_table("master_lists", schema="sbam")
