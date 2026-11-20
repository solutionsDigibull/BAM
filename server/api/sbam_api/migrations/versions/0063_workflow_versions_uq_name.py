"""workflow_versions: the unique (workflow_id, version) takes its naming-convention name

Revision ID: 0063_workflow_versions_uq_name
Revises: 0062_workflow_versions
Create Date: 2026-11-20

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#920: 0062 named the constraint `uq_workflow_versions_workflow_id`, but the model's naming
convention (`db/base.py`) gives `uq_workflow_versions_workflow_id_version`, so the no-drift check
(E1.5-T01) failed on every stack run. Renaming keeps the constraint and its index; no data moves.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0063_workflow_versions_uq_name"
down_revision: str | Sequence[str] | None = "0062_workflow_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "uq_workflow_versions_workflow_id"
_NEW = "uq_workflow_versions_workflow_id_version"


def upgrade() -> None:
    op.execute(f"ALTER TABLE sbam.workflow_versions RENAME CONSTRAINT {_OLD} TO {_NEW}")


def downgrade() -> None:
    op.execute(f"ALTER TABLE sbam.workflow_versions RENAME CONSTRAINT {_NEW} TO {_OLD}")
