"""master_documents: the parse result; master_snapshots: the parsed master documents (E3.8b)

Revision ID: 0028_master_document_parse
Revises: 0027_document_archive_path
Create Date: 2026-10-27

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_master_document_parse"
down_revision: str | Sequence[str] | None = "0027_document_archive_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns: list[sa.Column] = [  # type: ignore[type-arg]
        sa.Column("parse_code", sa.String(length=40), nullable=True),
        sa.Column("parse_report", postgresql.JSONB(), nullable=True),
        sa.Column("derived_md_key", sa.String(length=512), nullable=True),
        sa.Column("derived_json_key", sa.String(length=512), nullable=True),
    ]
    for column in columns:
        op.add_column("master_documents", column, schema="sbam")
    op.add_column(
        "master_snapshots",
        sa.Column(
            "documents", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("master_snapshots", "documents", schema="sbam")
    for name in ("derived_json_key", "derived_md_key", "parse_report", "parse_code"):
        op.drop_column("master_documents", name, schema="sbam")
