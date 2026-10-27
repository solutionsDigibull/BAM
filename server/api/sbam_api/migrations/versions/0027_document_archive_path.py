"""documents: the path of an archive member inside its archive (E4.7)

Revision ID: 0027_document_archive_path
Revises: 0026_document_parent
Create Date: 2026-10-27

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_document_archive_path"
down_revision: str | Sequence[str] | None = "0026_document_parent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("archive_path", sa.String(length=1024), nullable=True),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("documents", "archive_path", schema="sbam")
