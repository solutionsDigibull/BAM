"""workflow_schemas, workflow-level gate_results and SCHEMA_SQL artifacts

Revision ID: 0036_workflow_schemas
Revises: 0035_sub_skill_reuse
Create Date: 2026-10-28 16:32:53.046190

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E9.4: the versions of a Workflow's foundational schema.sql, Workflow-level gate results (SCHEMA_SQL
has no Sub-Skill: `sub_skill_id` becomes nullable, `workflow_id` is added and one of them must be
set), and the SCHEMA_SQL artifact kind.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_workflow_schemas"
down_revision: str | Sequence[str] | None = "0035_sub_skill_reuse"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_schemas",
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "tables",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "findings",
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
        sa.CheckConstraint(
            "source IN ('UPLOADED', 'GENERATED')", name=op.f("ck_workflow_schemas_source_valid")
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PASSED', 'FAILED', 'SKIPPED')",
            name=op.f("ck_workflow_schemas_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["sbam.documents.id"],
            name=op.f("fk_workflow_schemas_source_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["sbam.workflows.id"],
            name=op.f("fk_workflow_schemas_workflow_id_workflows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_schemas")),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_id",
            "version",
            name=op.f("uq_workflow_schemas_tenant_id_workflow_id_version"),
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_workflow_schemas_project_id"),
        "workflow_schemas",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_workflow_schemas_tenant_id"),
        "workflow_schemas",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_workflow_schemas_workflow_id"),
        "workflow_schemas",
        ["workflow_id"],
        unique=False,
        schema="sbam",
    )
    op.add_column("gate_results", sa.Column("workflow_id", sa.Uuid(), nullable=True), schema="sbam")
    op.alter_column(
        "gate_results", "sub_skill_id", existing_type=sa.UUID(), nullable=True, schema="sbam"
    )
    op.create_index(
        op.f("ix_gate_results_workflow_id"),
        "gate_results",
        ["workflow_id"],
        unique=False,
        schema="sbam",
    )
    op.create_foreign_key(
        op.f("fk_gate_results_workflow_id_workflows"),
        "gate_results",
        "workflows",
        ["workflow_id"],
        ["id"],
        source_schema="sbam",
        referent_schema="sbam",
    )
    op.create_check_constraint(
        op.f("ck_gate_results_has_subject"),
        "gate_results",
        "sub_skill_id IS NOT NULL OR workflow_id IS NOT NULL",
        schema="sbam",
    )
    op.drop_constraint(op.f("ck_artifacts_kind_valid"), "artifacts", schema="sbam", type_="check")
    op.create_check_constraint(
        op.f("ck_artifacts_kind_valid"),
        "artifacts",
        "kind IN ('CODE_BUNDLE', 'SCHEMA_SQL')",
        schema="sbam",
    )


def downgrade() -> None:
    # rows this revision's shapes allowed go first, or the old constraints can't come back
    op.execute("DELETE FROM sbam.artifacts WHERE kind = 'SCHEMA_SQL'")
    op.drop_constraint(op.f("ck_artifacts_kind_valid"), "artifacts", schema="sbam", type_="check")
    op.create_check_constraint(
        op.f("ck_artifacts_kind_valid"), "artifacts", "kind IN ('CODE_BUNDLE')", schema="sbam"
    )
    op.execute("DELETE FROM sbam.gate_results WHERE sub_skill_id IS NULL")
    op.drop_constraint(
        op.f("ck_gate_results_has_subject"), "gate_results", schema="sbam", type_="check"
    )
    op.drop_constraint(
        op.f("fk_gate_results_workflow_id_workflows"),
        "gate_results",
        schema="sbam",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_gate_results_workflow_id"), table_name="gate_results", schema="sbam")
    op.alter_column(
        "gate_results", "sub_skill_id", existing_type=sa.UUID(), nullable=False, schema="sbam"
    )
    op.drop_column("gate_results", "workflow_id", schema="sbam")
    op.drop_index(
        op.f("ix_workflow_schemas_workflow_id"), table_name="workflow_schemas", schema="sbam"
    )
    op.drop_index(
        op.f("ix_workflow_schemas_tenant_id"), table_name="workflow_schemas", schema="sbam"
    )
    op.drop_index(
        op.f("ix_workflow_schemas_project_id"), table_name="workflow_schemas", schema="sbam"
    )
    op.drop_table("workflow_schemas", schema="sbam")
