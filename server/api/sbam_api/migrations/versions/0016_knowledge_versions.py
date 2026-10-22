"""knowledge_versions and knowledge_sources

Revision ID: 0016_knowledge_versions
Revises: 0015_llm_calls
Create Date: 2026-10-16

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E5.2: one row per knowledge build of a project (BUILDING -> READY | FAILED, READY -> OUTDATED) and
the document versions each build used. At most one BUILDING version per project (partial index).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_knowledge_versions"
down_revision: str | Sequence[str] | None = "0015_llm_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_versions",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("cognee_dataset", sa.String(length=120), nullable=False),
        sa.Column("master_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("okf_bundle_key", sa.String(length=255), nullable=True),
        sa.Column("okf_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "coverage",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("error_reason", sa.String(length=300), nullable=True),
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
            "state IN ('BUILDING', 'READY', 'FAILED', 'OUTDATED')",
            name=op.f("ck_knowledge_versions_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["master_snapshot_id"],
            ["sbam.master_snapshots.id"],
            name=op.f("fk_knowledge_versions_master_snapshot_id_master_snapshots"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_versions")),
        sa.UniqueConstraint(
            "project_id", "version", name=op.f("uq_knowledge_versions_project_id_version")
        ),
        sa.UniqueConstraint("run_id", name=op.f("uq_knowledge_versions_run_id")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_knowledge_versions_project_id"),
        "knowledge_versions",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_knowledge_versions_state"),
        "knowledge_versions",
        ["state"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_knowledge_versions_tenant_id"),
        "knowledge_versions",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        "uq_knowledge_versions_one_building",
        "knowledge_versions",
        ["project_id"],
        unique=True,
        schema="sbam",
        postgresql_where=sa.text("state = 'BUILDING'"),
        sqlite_where=sa.text("state = 'BUILDING'"),
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("knowledge_version_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
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
            "status IN ('ADDED', 'FAILED')", name=op.f("ck_knowledge_sources_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["sbam.document_versions.id"],
            name=op.f("fk_knowledge_sources_document_version_id_document_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_version_id"],
            ["sbam.knowledge_versions.id"],
            name=op.f("fk_knowledge_sources_knowledge_version_id_knowledge_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sources")),
        sa.UniqueConstraint(
            "knowledge_version_id",
            "document_version_id",
            name=op.f("uq_knowledge_sources_knowledge_version_id_document_version_id"),
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_knowledge_sources_knowledge_version_id"),
        "knowledge_sources",
        ["knowledge_version_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_knowledge_sources_project_id"),
        "knowledge_sources",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_knowledge_sources_tenant_id"),
        "knowledge_sources",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_knowledge_sources_tenant_id"), table_name="knowledge_sources", schema="sbam"
    )
    op.drop_index(
        op.f("ix_knowledge_sources_project_id"), table_name="knowledge_sources", schema="sbam"
    )
    op.drop_index(
        op.f("ix_knowledge_sources_knowledge_version_id"),
        table_name="knowledge_sources",
        schema="sbam",
    )
    op.drop_table("knowledge_sources", schema="sbam")
    op.drop_index(
        "uq_knowledge_versions_one_building",
        table_name="knowledge_versions",
        schema="sbam",
        postgresql_where=sa.text("state = 'BUILDING'"),
        sqlite_where=sa.text("state = 'BUILDING'"),
    )
    op.drop_index(
        op.f("ix_knowledge_versions_tenant_id"), table_name="knowledge_versions", schema="sbam"
    )
    op.drop_index(
        op.f("ix_knowledge_versions_state"), table_name="knowledge_versions", schema="sbam"
    )
    op.drop_index(
        op.f("ix_knowledge_versions_project_id"), table_name="knowledge_versions", schema="sbam"
    )
    op.drop_table("knowledge_versions", schema="sbam")
