"""sow_versions content is immutable

Revision ID: 0020_sow_versions_immutable
Revises: 0019_sow_versions
Create Date: 2026-10-22

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E6.3: a trigger rejects DELETE, TRUNCATE and any UPDATE of a version's content columns
(`sbam_api.sow.sql`); state and approval columns still change through the state machine.
"""

from collections.abc import Sequence

from alembic import op
from sbam_api.sow.sql import create_immutable_guard, drop_immutable_guard

revision: str = "0020_sow_versions_immutable"
down_revision: str | Sequence[str] | None = "0019_sow_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in create_immutable_guard():
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_immutable_guard():
        op.execute(statement)
