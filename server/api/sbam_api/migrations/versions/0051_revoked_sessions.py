"""revoked_sessions: sessions that signed out, refused until their tokens expire

Revision ID: 0051_revoked_sessions
Revises: 0050_certifications
Create Date: 2026-11-03

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

A-32 (#458): one row per signed-out Keycloak session (`sid`). Not tenant data and never listed:
the API only asks whether one `sid` is there. Rows expire on their own (`expires_at`) and are swept
by the next sign-out.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_revoked_sessions"
down_revision: str | Sequence[str] | None = "0050_certifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revoked_sessions",
        sa.Column("sid", sa.String(length=64), nullable=False),
        sa.Column("keycloak_sub", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sid", name=op.f("pk_revoked_sessions")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_revoked_sessions_expires_at"),
        "revoked_sessions",
        ["expires_at"],
        unique=False,
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_revoked_sessions_expires_at"), table_name="revoked_sessions", schema="sbam"
    )
    op.drop_table("revoked_sessions", schema="sbam")
