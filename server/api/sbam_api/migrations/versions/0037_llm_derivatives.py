"""llm_derivatives: content-addressed LLM answers, reused instead of paying twice

Revision ID: 0037_llm_derivatives
Revises: 0036_workflow_schemas
Create Date: 2026-10-28

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_llm_derivatives"
down_revision: str | Sequence[str] | None = "0036_workflow_schemas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "llm_derivatives",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("alias", sa.String(length=40), nullable=False),
        sa.Column("prompt_id", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("output", _JSON, nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
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
            "row_version >= 1", name=op.f("ck_llm_derivatives_row_version_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_derivatives")),
        sa.UniqueConstraint(
            "tenant_id", "cache_key", name=op.f("uq_llm_derivatives_tenant_id_cache_key")
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_llm_derivatives_tenant_id"), "llm_derivatives", ["tenant_id"], schema="sbam"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_derivatives_tenant_id"), table_name="llm_derivatives", schema="sbam")
    op.drop_table("llm_derivatives", schema="sbam")
