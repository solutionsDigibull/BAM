"""tenant_srim_defaults

Revision ID: 0044_tenant_srim_defaults
Revises: 0043_dataset_generated_for
Create Date: 2026-10-30

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E9.1b: a tenant's own SRIM runtime defaults (O-19). One row per tenant that set them; a tenant
without a row gets the ISA14 values, so nothing is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_tenant_srim_defaults"
down_revision: str | Sequence[str] | None = "0043_dataset_generated_for"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_srim_defaults",
        sa.Column(
            "defaults",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_srim_defaults")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_tenant_srim_defaults_tenant_id")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_tenant_srim_defaults_tenant_id"),
        "tenant_srim_defaults",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_tenant_srim_defaults_tenant_id"), table_name="tenant_srim_defaults", schema="sbam"
    )
    op.drop_table("tenant_srim_defaults", schema="sbam")
