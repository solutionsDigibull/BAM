"""dataset_generated_for

Revision ID: 0043_dataset_generated_for
Revises: 0042_test_runs
Create Date: 2026-10-29

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E11.4b: what a test dataset was generated for (a hash of the Sub-Skill's `max_iterations`, closed
input schema and tables), so a correction that changes the bounds regenerates the tests and one that
does not leaves them alone. Null for a dataset written before this column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_dataset_generated_for"
down_revision: str | Sequence[str] | None = "0042_test_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_datasets",
        sa.Column("generated_for", sa.String(length=64), nullable=True),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("test_datasets", "generated_for", schema="sbam")
