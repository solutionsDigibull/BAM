"""certifications

Revision ID: 0050_certifications
Revises: 0049_sandbox_runs
Create Date: 2026-11-03

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E14.1: the certificate of one subject on one content hash, inserted frozen so the shared
`forbid_frozen_change` trigger (0001) makes it immutable. `(level, subject_id, content_sha256)` is
unique, which is what makes the certify activity idempotent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sbam_api.db.frozen import create_frozen_trigger, drop_frozen_trigger
from sqlalchemy.dialects import postgresql

revision: str = "0050_certifications"
down_revision: str | Sequence[str] | None = "0049_sandbox_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "certifications",
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(length=71), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_frozen", sa.Boolean(), server_default="true", nullable=False),
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
            "level IN ('SUB_SKILL', 'MACRO_SKILL', 'WORKFLOW')",
            name=op.f("ck_certifications_level_valid"),
        ),
        sa.CheckConstraint("is_frozen", name=op.f("ck_certifications_always_frozen")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certifications")),
        sa.UniqueConstraint(
            "tenant_id",
            "level",
            "subject_id",
            "content_sha256",
            name=op.f("uq_certifications_tenant_id"),
        ),
        schema="sbam",
    )
    for column in ("project_id", "tenant_id", "subject_id"):
        op.create_index(
            op.f(f"ix_certifications_{column}"),
            "certifications",
            [column],
            unique=False,
            schema="sbam",
        )
    op.execute(create_frozen_trigger("certifications"))


def downgrade() -> None:
    op.execute(drop_frozen_trigger("certifications"))
    for column in ("subject_id", "tenant_id", "project_id"):
        op.drop_index(
            op.f(f"ix_certifications_{column}"), table_name="certifications", schema="sbam"
        )
    op.drop_table("certifications", schema="sbam")
