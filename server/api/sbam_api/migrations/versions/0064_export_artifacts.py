"""artifacts kind EXPORT

Revision ID: 0064_export_artifacts
Revises: 0063_workflow_versions_uq_name
Create Date: 2026-11-19

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#133 (E14.5): the export zip of a frozen Workflow version is indexed in `artifacts` as kind EXPORT
(stored frozen, with its sha256).
"""

from collections.abc import Sequence

from alembic import op
from sbam_api.db.frozen import create_frozen_trigger, drop_frozen_trigger

revision: str = "0064_export_artifacts"
down_revision: str | Sequence[str] | None = "0063_workflow_versions_uq_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = (
    "kind IN ('CODE_BUNDLE', 'SCHEMA_SQL', 'SANDBOX_OUTPUTS', 'SANDBOX_LOGS', 'SANDBOX_EGRESS')"
)
AFTER = (
    "kind IN ('CODE_BUNDLE', 'SCHEMA_SQL', 'SANDBOX_OUTPUTS', 'SANDBOX_LOGS', 'SANDBOX_EGRESS',"
    " 'EXPORT')"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_artifacts_kind_valid"), "artifacts", schema="sbam", type_="check")
    op.create_check_constraint(op.f("ck_artifacts_kind_valid"), "artifacts", AFTER, schema="sbam")


def downgrade() -> None:
    # EXPORT rows are frozen: lift the trigger for this one delete, or the old constraint can't
    # come back
    op.execute(drop_frozen_trigger("artifacts"))
    op.execute("DELETE FROM sbam.artifacts WHERE kind = 'EXPORT'")
    op.execute(create_frozen_trigger("artifacts"))
    op.drop_constraint(op.f("ck_artifacts_kind_valid"), "artifacts", schema="sbam", type_="check")
    op.create_check_constraint(op.f("ck_artifacts_kind_valid"), "artifacts", BEFORE, schema="sbam")
