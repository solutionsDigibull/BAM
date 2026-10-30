"""dataset_mocks_keys

Revision ID: 0045_dataset_mocks_keys
Revises: 0044_tenant_srim_defaults
Create Date: 2026-10-30

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E13.5: the mock definitions of a dataset's API references, stored content-addressed like its files.
Datasets written before this column have none: `'[]'`, which is also what hashes the same as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_dataset_mocks_keys"
down_revision: str | Sequence[str] | None = "0044_tenant_srim_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "test_datasets",
        sa.Column("mocks_keys", JSON, server_default=sa.text("'[]'"), nullable=False),
        schema="sbam",
    )
    op.alter_column("test_datasets", "mocks_keys", server_default=None, schema="sbam")


def downgrade() -> None:
    op.drop_column("test_datasets", "mocks_keys", schema="sbam")
