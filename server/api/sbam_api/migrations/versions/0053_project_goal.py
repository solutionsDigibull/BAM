"""projects: goal

Revision ID: 0053_project_goal
Revises: 0052_token_budget_notifications
Create Date: 2026-11-16

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#720: the FDE's goal for the project, one sentence of at most 300 characters. Nullable: existing
projects have none until an FDE sets it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_project_goal"
down_revision: str | Sequence[str] | None = "0052_token_budget_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects", sa.Column("goal", sa.String(length=300), nullable=True), schema="sbam"
    )


def downgrade() -> None:
    op.drop_column("projects", "goal", schema="sbam")
