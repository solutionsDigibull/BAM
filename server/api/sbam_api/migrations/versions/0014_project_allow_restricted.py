"""projects: allow_restricted

Revision ID: 0014_project_allow_restricted
Revises: 0013_registry_allowed
Create Date: 2026-10-15

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E3.6: whether Restricted master entries may enter a project's knowledge context. Default false, so
existing projects keep excluding them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_project_allow_restricted"
down_revision: str | Sequence[str] | None = "0013_registry_allowed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("allow_restricted", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("projects", "allow_restricted", schema="sbam")
