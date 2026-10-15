"""llm_calls: one row per LLM call attempt (E6.1)

Revision ID: 0014_llm_calls
Revises: 0013_registry_allowed
Create Date: 2026-10-15

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_llm_calls"
down_revision: str | Sequence[str] | None = "0013_registry_allowed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("alias", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("prompt_id", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("context_manifest", _JSON, nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("temporal_workflow_id", sa.String(length=255), nullable=False),
        sa.Column("activity_id", sa.String(length=255), nullable=False),
        sa.Column("activity_attempt", sa.Integer(), nullable=False),
        sa.Column("output", _JSON, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_llm_calls_row_version_positive")),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'SCHEMA_INVALID', 'FAILED')",
            name=op.f("ck_llm_calls_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
        schema="sbam",
    )
    op.create_index(op.f("ix_llm_calls_tenant_id"), "llm_calls", ["tenant_id"], schema="sbam")
    op.create_index(op.f("ix_llm_calls_project_id"), "llm_calls", ["project_id"], schema="sbam")
    op.create_index(
        "ix_llm_calls_temporal_workflow_id_activity_id",
        "llm_calls",
        ["temporal_workflow_id", "activity_id"],
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_calls_temporal_workflow_id_activity_id", table_name="llm_calls", schema="sbam"
    )
    op.drop_index(op.f("ix_llm_calls_project_id"), table_name="llm_calls", schema="sbam")
    op.drop_index(op.f("ix_llm_calls_tenant_id"), table_name="llm_calls", schema="sbam")
    op.drop_table("llm_calls", schema="sbam")
