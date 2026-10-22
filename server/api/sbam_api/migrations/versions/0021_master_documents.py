"""master_documents: context files an Admin attaches to a master (E3.8)

Revision ID: 0021_master_documents
Revises: 0020_sow_versions_immutable
Create Date: 2026-10-22

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_master_documents"
down_revision: str | Sequence[str] | None = "0020_sow_versions_immutable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MASTERS = "'ORGANIZATION', 'RULES', 'AGENTS', 'SKILLS'"
_SOURCES = "'DEFAULT_BY_FILETYPE', 'ADMIN_OVERRIDE', 'CLASSIFIER'"
_SCANS = "'CLEAN', 'INFECTED', 'SCAN_FAILED'"
_PARSES = "'PENDING', 'PARSING', 'PARSED', 'FAILED'"
_TENANT_SCOPE = "project_id IS NULL AND deleted_at IS NULL"
_PROJECT_SCOPE = "project_id IS NOT NULL AND deleted_at IS NULL"


def upgrade() -> None:
    op.create_table(
        "master_documents",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("filetype_row_label", sa.String(length=40), nullable=False),
        sa.Column("intent_class", sa.String(length=100), nullable=False),
        sa.Column("master_type", sa.String(length=20), nullable=False),
        sa.Column("master_entry_id", sa.Uuid(), nullable=True),
        sa.Column("route_source", sa.String(length=30), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("scan_status", sa.String(length=20), nullable=True),
        sa.Column("scan_signature_version", sa.String(length=100), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parse_status", sa.String(length=20), nullable=False),
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
            "row_version >= 1", name=op.f("ck_master_documents_row_version_positive")
        ),
        sa.CheckConstraint(
            f"master_type IN ({_MASTERS})", name=op.f("ck_master_documents_master_type_valid")
        ),
        sa.CheckConstraint(
            f"route_source IN ({_SOURCES})", name=op.f("ck_master_documents_route_source_valid")
        ),
        sa.CheckConstraint(
            f"scan_status IN ({_SCANS})", name=op.f("ck_master_documents_scan_status_valid")
        ),
        sa.CheckConstraint(
            f"parse_status IN ({_PARSES})", name=op.f("ck_master_documents_parse_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["sbam.projects.id"],
            name=op.f("fk_master_documents_project_id_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["master_entry_id"],
            ["sbam.master_entries.id"],
            name=op.f("fk_master_documents_master_entry_id_master_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_documents")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_master_documents_tenant_id"),
        "master_documents",
        ["tenant_id"],
        schema="sbam",
    )
    op.create_index(
        op.f("ix_master_documents_project_id"),
        "master_documents",
        ["project_id"],
        schema="sbam",
    )
    op.create_index(
        "uq_master_documents_tenant_sha256",
        "master_documents",
        ["tenant_id", "sha256"],
        unique=True,
        schema="sbam",
        postgresql_where=sa.text(_TENANT_SCOPE),
    )
    op.create_index(
        "uq_master_documents_project_sha256",
        "master_documents",
        ["tenant_id", "project_id", "sha256"],
        unique=True,
        schema="sbam",
        postgresql_where=sa.text(_PROJECT_SCOPE),
    )


def downgrade() -> None:
    op.drop_table("master_documents", schema="sbam")
