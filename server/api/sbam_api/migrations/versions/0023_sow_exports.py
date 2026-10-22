"""sow_exports

Revision ID: 0023_sow_exports
Revises: 0022_code_bundles
Create Date: 2026-10-22

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E6.4: rendered SOW exports, cached by (version, status, format, template version).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_sow_exports"
down_revision: str | Sequence[str] | None = "0022_code_bundles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sow_exports",
        sa.Column("sow_version_id", sa.Uuid(), nullable=False),
        sa.Column("sow_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("template_version", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
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
            ["sow_version_id"],
            ["sbam.sow_versions.id"],
            name=op.f("fk_sow_exports_sow_version_id_sow_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sow_exports")),
        sa.UniqueConstraint(
            "sow_version_id",
            "status",
            "format",
            "template_version",
            name=op.f("uq_sow_exports_sow_version_id_status_format_template_version"),
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sow_exports_project_id"),
        "sow_exports",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sow_exports_sow_version_id"),
        "sow_exports",
        ["sow_version_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sow_exports_tenant_id"), "sow_exports", ["tenant_id"], unique=False, schema="sbam"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sow_exports_tenant_id"), table_name="sow_exports", schema="sbam")
    op.drop_index(op.f("ix_sow_exports_sow_version_id"), table_name="sow_exports", schema="sbam")
    op.drop_index(op.f("ix_sow_exports_project_id"), table_name="sow_exports", schema="sbam")
    op.drop_table("sow_exports", schema="sbam")
