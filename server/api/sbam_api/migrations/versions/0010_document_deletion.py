"""documents.deleted_by and an index on documents.purge_after

Revision ID: 0010_document_deletion
Revises: 0009_document_scan
Create Date: 2026-10-15

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_document_deletion"
down_revision: str | Sequence[str] | None = "0009_document_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("deleted_by", sa.String(length=255), nullable=True), schema="sbam"
    )
    op.create_index("ix_documents_purge_after", "documents", ["purge_after"], schema="sbam")


def downgrade() -> None:
    op.drop_index("ix_documents_purge_after", table_name="documents", schema="sbam")
    op.drop_column("documents", "deleted_by", schema="sbam")
