"""filetype_row_versions: super-admin versions of the 43 platform Filetype rows (E3.13, ADR 0020)

Revision ID: 0028_filetype_row_versions
Revises: 0027_document_archive_path
Create Date: 2026-10-27

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

Append-only through the same trigger function as the masters version tables (0011).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_filetype_row_versions"
down_revision: str | Sequence[str] | None = "0027_document_archive_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = '"sbam"."filetype_row_versions"'
FUNCTION = '"sbam".forbid_masters_change()'


def upgrade() -> None:
    op.create_table(
        "filetype_row_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("row_label", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workbook_sha256", sa.String(length=71), nullable=False),
        sa.Column("version_label", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filetype_row_versions")),
        sa.UniqueConstraint(
            "row_label", "version", name=op.f("uq_filetype_row_versions_row_label_version")
        ),
        schema="sbam",
    )
    op.execute(
        'CREATE TRIGGER "filetype_row_versions_append_only" BEFORE UPDATE OR DELETE ON '
        f"{TABLE} FOR EACH ROW EXECUTE FUNCTION {FUNCTION}"
    )
    op.execute(
        f'CREATE TRIGGER "filetype_row_versions_no_truncate" BEFORE TRUNCATE ON {TABLE} '
        f"FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}"
    )


def downgrade() -> None:
    op.execute(f'DROP TRIGGER IF EXISTS "filetype_row_versions_no_truncate" ON {TABLE}')
    op.execute(f'DROP TRIGGER IF EXISTS "filetype_row_versions_append_only" ON {TABLE}')
    op.drop_table("filetype_row_versions", schema="sbam")
