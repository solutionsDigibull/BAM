"""document_versions: relevance

Revision ID: 0054_document_relevance
Revises: 0053_project_goal
Create Date: 2026-11-17

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#802: how useful a parsed document is for the project's goal, as the model judged it
(`DocumentRelevance`: level, score, reason, goal_sha256, scored_at). NULL until scored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054_document_relevance"
down_revision: str | Sequence[str] | None = "0053_project_goal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("relevance", postgresql.JSONB(), nullable=True),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("document_versions", "relevance", schema="sbam")
