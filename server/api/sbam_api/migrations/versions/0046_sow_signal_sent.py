"""sow_versions.signal_sent_at, signal_claimed_at

Revision ID: 0046_sow_signal_sent
Revises: 0045_dataset_mocks_keys
Create Date: 2026-10-30

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

A-03: the approval is committed first and the pipeline is told afterwards, so an approved version
whose pipeline has not been told yet must be findable. `signal_sent_at` is NULL until it was told;
`signal_claimed_at` says who is telling it now.
Every version approved before this revision was approved only after Temporal took the signal, so
those are backfilled as sent. The content trigger of 0020 only guards content columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_sow_signal_sent"
down_revision: str | Sequence[str] | None = "0045_dataset_mocks_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sow_versions",
        sa.Column("signal_sent_at", sa.DateTime(timezone=True), nullable=True),
        schema="sbam",
    )
    op.add_column(
        "sow_versions",
        sa.Column("signal_claimed_at", sa.DateTime(timezone=True), nullable=True),
        schema="sbam",
    )
    op.execute(
        "UPDATE sbam.sow_versions SET signal_sent_at = approved_at WHERE approved_at IS NOT NULL"
    )
    op.create_index(
        "ix_sow_versions_signal_pending",
        "sow_versions",
        ["approved_at"],
        schema="sbam",
        postgresql_where=sa.text("approved_at IS NOT NULL AND signal_sent_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_sow_versions_signal_pending", table_name="sow_versions", schema="sbam")
    op.drop_column("sow_versions", "signal_claimed_at", schema="sbam")
    op.drop_column("sow_versions", "signal_sent_at", schema="sbam")
