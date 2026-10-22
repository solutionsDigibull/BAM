"""document_versions: the detected type and its Filetype row (E4.4)

Revision ID: 0017_document_type_detection
Revises: 0016_knowledge_versions
Create Date: 2026-10-22

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_document_type_detection"
down_revision: str | Sequence[str] | None = "0016_knowledge_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("detected_type", sa.String(length=40), nullable=True),
        schema="sbam",
    )
    op.add_column(
        "document_versions",
        sa.Column("filetype_row_label", sa.String(length=40), nullable=True),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("document_versions", "filetype_row_label", schema="sbam")
    op.drop_column("document_versions", "detected_type", schema="sbam")
