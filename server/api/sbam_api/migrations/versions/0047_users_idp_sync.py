"""users.idp_sync_pending_at, idp_synced_roles

Revision ID: 0047_users_idp_sync
Revises: 0046_sow_signal_sent
Create Date: 2026-10-30

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

A-13: a role change or deactivation is committed first and Keycloak is converged afterwards, so a
user whose Keycloak account has not caught up yet must be findable. `idp_sync_pending_at` is NULL
when Keycloak agrees with the row; `idp_synced_roles` is what Keycloak held before the change.
Every existing row is in sync, so nothing is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_users_idp_sync"
down_revision: str | Sequence[str] | None = "0046_sow_signal_sent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("idp_sync_pending_at", sa.DateTime(timezone=True), nullable=True),
        schema="sbam",
    )
    op.add_column(
        "users",
        sa.Column("idp_synced_roles", postgresql.ARRAY(sa.String(40)), nullable=True),
        schema="sbam",
    )
    op.create_index(
        "ix_users_idp_sync_pending",
        "users",
        ["idp_sync_pending_at"],
        schema="sbam",
        postgresql_where=sa.text("idp_sync_pending_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_idp_sync_pending", table_name="users", schema="sbam")
    op.drop_column("users", "idp_synced_roles", schema="sbam")
    op.drop_column("users", "idp_sync_pending_at", schema="sbam")
