"""masters engine

Revision ID: 0009_masters
Revises: 0008_rbac
Create Date: 2026-10-15

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sbam_api.masters.sql import create_append_only_guard, drop_append_only_guard
from sqlalchemy.dialects import postgresql

revision: str = "0009_masters"
down_revision: str | Sequence[str] | None = "0008_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPES = "'ORGANIZATION', 'RULES', 'AGENTS', 'SKILLS', 'TOOLING', 'FILETYPE'"
_STATUSES = "'PROPOSED', 'ILLUSTRATIVE', 'APPROVED', 'RETIRED'"
_KINDS = "'BAM_0_2_SAMPLE', 'TEMPLATE_ROW', 'ADMIN_ENTRY'"


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "master_entries",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("master_type", sa.String(length=20), nullable=False),
        sa.Column("stable_id", sa.String(length=64), nullable=False),
        sa.Column("row_label", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_master_entries_row_version_positive")),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name=op.f("ck_master_entries_status_valid")),
        sa.CheckConstraint(
            f"master_type IN ({_TYPES})", name=op.f("ck_master_entries_master_type_valid")
        ),
        sa.CheckConstraint(
            f"source_kind IN ({_KINDS})", name=op.f("ck_master_entries_source_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["sbam.projects.id"],
            name=op.f("fk_master_entries_project_id_projects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_entries")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_master_entries_project_id"),
        "master_entries",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_master_entries_tenant_id"),
        "master_entries",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        "uq_master_entries_tenant_stable_id",
        "master_entries",
        ["tenant_id", "master_type", "stable_id"],
        unique=True,
        schema="sbam",
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        "uq_master_entries_project_stable_id",
        "master_entries",
        ["tenant_id", "project_id", "master_type", "stable_id"],
        unique=True,
        schema="sbam",
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )

    op.create_table(
        "master_entry_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["sbam.master_entries.id"],
            name=op.f("fk_master_entry_versions_entry_id_master_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_entry_versions")),
        sa.UniqueConstraint(
            "entry_id", "version", name=op.f("uq_master_entry_versions_entry_id_version")
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_master_entry_versions_tenant_id"),
        "master_entry_versions",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )

    op.create_table(
        "master_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "entries", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("sha256", sa.String(length=71), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["sbam.projects.id"],
            name=op.f("fk_master_snapshots_project_id_projects"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_snapshots")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_master_snapshots_project_id"),
        "master_snapshots",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_master_snapshots_tenant_id"),
        "master_snapshots",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    for statement in create_append_only_guard():
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_append_only_guard():
        op.execute(statement)
    op.drop_index(
        op.f("ix_master_snapshots_tenant_id"), table_name="master_snapshots", schema="sbam"
    )
    op.drop_index(
        op.f("ix_master_snapshots_project_id"), table_name="master_snapshots", schema="sbam"
    )
    op.drop_table("master_snapshots", schema="sbam")
    op.drop_index(
        op.f("ix_master_entry_versions_tenant_id"),
        table_name="master_entry_versions",
        schema="sbam",
    )
    op.drop_table("master_entry_versions", schema="sbam")
    op.drop_index("uq_master_entries_project_stable_id", table_name="master_entries", schema="sbam")
    op.drop_index("uq_master_entries_tenant_stable_id", table_name="master_entries", schema="sbam")
    op.drop_index(op.f("ix_master_entries_tenant_id"), table_name="master_entries", schema="sbam")
    op.drop_index(op.f("ix_master_entries_project_id"), table_name="master_entries", schema="sbam")
    op.drop_table("master_entries", schema="sbam")
