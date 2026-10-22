"""sows and sow_versions

Revision ID: 0019_sow_versions
Revises: 0018_document_type_detection
Create Date: 2026-10-22

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E6.2: one `sows` row per project and its immutable versions (GENERATED, IN_REVIEW, APPROVED,
SUPERSEDED, GENERATION_FAILED). `(run_id, knowledge_version_id)` is unique so a retried generation
activity finds the version it already wrote.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_sow_versions"
down_revision: str | Sequence[str] | None = "0018_document_type_detection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sows",
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("approved_version_id", sa.Uuid(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sows")),
        sa.UniqueConstraint("project_id", name=op.f("uq_sows_project_id")),
        schema="sbam",
    )
    op.create_index(op.f("ix_sows_project_id"), "sows", ["project_id"], unique=False, schema="sbam")
    op.create_index(op.f("ix_sows_tenant_id"), "sows", ["tenant_id"], unique=False, schema="sbam")
    op.create_table(
        "sow_versions",
        sa.Column("sow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_version_id", sa.Uuid(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("llm_call_id", sa.Uuid(), nullable=True),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column(
            "structured",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "content_tiptap",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("error_reason", sa.String(length=300), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
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
            "source IN ('GENERATED', 'EDITED')", name=op.f("ck_sow_versions_source_valid")
        ),
        sa.CheckConstraint(
            "state IN ('GENERATED', 'IN_REVIEW', 'APPROVED', 'SUPERSEDED', 'GENERATION_FAILED')",
            name=op.f("ck_sow_versions_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_version_id"],
            ["sbam.knowledge_versions.id"],
            name=op.f("fk_sow_versions_knowledge_version_id_knowledge_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["llm_call_id"],
            ["sbam.llm_calls.id"],
            name=op.f("fk_sow_versions_llm_call_id_llm_calls"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["sbam.sow_versions.id"],
            name=op.f("fk_sow_versions_parent_version_id_sow_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["sow_id"], ["sbam.sows.id"], name=op.f("fk_sow_versions_sow_id_sows")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sow_versions")),
        sa.UniqueConstraint(
            "run_id",
            "knowledge_version_id",
            name=op.f("uq_sow_versions_run_id_knowledge_version_id"),
        ),
        sa.UniqueConstraint("sow_id", "version", name=op.f("uq_sow_versions_sow_id_version")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sow_versions_project_id"),
        "sow_versions",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sow_versions_sow_id"), "sow_versions", ["sow_id"], unique=False, schema="sbam"
    )
    op.create_index(
        op.f("ix_sow_versions_state"), "sow_versions", ["state"], unique=False, schema="sbam"
    )
    op.create_index(
        op.f("ix_sow_versions_tenant_id"),
        "sow_versions",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sow_versions_tenant_id"), table_name="sow_versions", schema="sbam")
    op.drop_index(op.f("ix_sow_versions_state"), table_name="sow_versions", schema="sbam")
    op.drop_index(op.f("ix_sow_versions_sow_id"), table_name="sow_versions", schema="sbam")
    op.drop_index(op.f("ix_sow_versions_project_id"), table_name="sow_versions", schema="sbam")
    op.drop_table("sow_versions", schema="sbam")
    op.drop_index(op.f("ix_sows_tenant_id"), table_name="sows", schema="sbam")
    op.drop_index(op.f("ix_sows_project_id"), table_name="sows", schema="sbam")
    op.drop_table("sows", schema="sbam")
