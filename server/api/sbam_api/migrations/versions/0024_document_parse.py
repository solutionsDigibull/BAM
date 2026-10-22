"""document_versions: the parse report, the failure code and the derivative keys (E4.5)

Revision ID: 0024_document_parse
Revises: 0023_sow_exports
Create Date: 2026-10-22

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_document_parse"
down_revision: str | Sequence[str] | None = "0023_sow_exports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns: list[sa.Column] = [  # type: ignore[type-arg]
        sa.Column("parse_code", sa.String(length=40), nullable=True),
        sa.Column("parse_report", postgresql.JSONB(), nullable=True),
        sa.Column("derived_md_key", sa.String(length=255), nullable=True),
        sa.Column("derived_json_key", sa.String(length=255), nullable=True),
    ]
    for column in columns:
        op.add_column("document_versions", column, schema="sbam")


def downgrade() -> None:
    for name in ("derived_json_key", "derived_md_key", "parse_report", "parse_code"):
        op.drop_column("document_versions", name, schema="sbam")
