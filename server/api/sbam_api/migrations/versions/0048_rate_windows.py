"""rate_windows: the per-user request budget shared by several sbam-api replicas

Revision ID: 0048_rate_windows
Revises: 0047_users_idp_sync
Create Date: 2026-10-30

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

A-08 (#554): one counter per (bucket, minute), where bucket is `<kind>:<user id>`. UNLOGGED: a crash
may forget a minute of counts, which only lets a few extra requests through, and nothing is written
to the WAL. The API sweeps rows older than five minutes itself. Only used when SBAM_API_REPLICAS > 1.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_rate_windows"
down_revision: str | Sequence[str] | None = "0047_users_idp_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_windows",
        sa.Column("bucket", sa.String(80), nullable=False),
        sa.Column("minute", sa.BigInteger(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("bucket", "minute", name="pk_rate_windows"),
        schema="sbam",
        prefixes=["UNLOGGED"],
    )


def downgrade() -> None:
    op.drop_table("rate_windows", schema="sbam")
