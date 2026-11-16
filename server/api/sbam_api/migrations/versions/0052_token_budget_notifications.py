"""token budget notifications: two notification types for the 50% and 80% alerts

Revision ID: 0052_token_budget_notifications
Revises: 0051_revoked_sessions
Create Date: 2026-11-16

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

WG-5 (#753): `notifications.type` is a CHECK over the enum, so the two new types need the
constraint widened. Rows of the new types go first on downgrade, or the old constraint can't return.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0052_token_budget_notifications"
down_revision: str | Sequence[str] | None = "0051_revoked_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = (
    "'SOW_READY', 'BREAKDOWN_READY', 'SUBSKILL_NEEDS_REVIEW', 'WORKFLOW_CERTIFIED', "
    "'WORKFLOW_FROZEN', 'EXPORT_READY', 'DOCUMENT_REJECTED', 'GENERATION_FAILED', "
    "'STAGE_COMPLETED', 'REMINDER'"
)
_NEW = _OLD + ", 'TOKEN_BUDGET_WARNING', 'TOKEN_BUDGET_LIMIT'"
_NAME = op.f("ck_notifications_type_valid")


def _set(values: str) -> None:
    op.drop_constraint(_NAME, "notifications", schema="sbam", type_="check")
    op.create_check_constraint(_NAME, "notifications", f"type IN ({values})", schema="sbam")


def upgrade() -> None:
    _set(_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM sbam.notifications WHERE type LIKE 'TOKEN_BUDGET_%'")
    _set(_OLD)
