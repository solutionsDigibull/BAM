"""code_attempts

Revision ID: 0017_code_attempts
Revises: 0016_knowledge_versions
Create Date: 2026-10-22

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E11.2: one row per generate_bodies stage key (sub_skill_id, build_no, cycle, attempt) holding the
LLM bodies, their llm_calls ids and the pre-check findings.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_code_attempts"
down_revision: str | Sequence[str] | None = "0016_knowledge_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "code_attempts",
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("sub_skill_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_id", sa.Uuid(), nullable=True),
        sa.Column("build_no", sa.Integer(), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.String(length=100), nullable=False),
        sa.Column("template_version", sa.String(length=40), nullable=False),
        sa.Column("template_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "bodies",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "llm_call_ids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column(
            "findings",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("bundle_key", sa.String(length=1024), nullable=True),
        sa.Column(
            "files",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("correction_reason", sa.Text(), nullable=True),
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
            "status IN ('BODIES_GENERATED', 'PRECHECK_FAILED')",
            name=op.f("ck_code_attempts_status_valid"),
        ),
        sa.CheckConstraint(
            "attempt >= 0 AND attempt <= 2", name=op.f("ck_code_attempts_attempt_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_attempts")),
        sa.UniqueConstraint(
            "tenant_id",
            "sub_skill_id",
            "build_no",
            "cycle",
            "attempt",
            name=op.f("uq_code_attempts_tenant_id_sub_skill_id_build_no_cycle_attempt"),
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_code_attempts_project_id"),
        "code_attempts",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_code_attempts_sub_skill_id"),
        "code_attempts",
        ["sub_skill_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_code_attempts_tenant_id"),
        "code_attempts",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_code_attempts_tenant_id"), table_name="code_attempts", schema="sbam")
    op.drop_index(op.f("ix_code_attempts_sub_skill_id"), table_name="code_attempts", schema="sbam")
    op.drop_index(op.f("ix_code_attempts_project_id"), table_name="code_attempts", schema="sbam")
    op.drop_table("code_attempts", schema="sbam")
