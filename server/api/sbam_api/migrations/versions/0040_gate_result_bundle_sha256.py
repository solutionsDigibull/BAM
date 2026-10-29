"""gate_result_bundle_sha256

Revision ID: 0040_gate_result_bundle_sha256
Revises: 0039_workflow_manifests
Create Date: 2026-10-29

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E12.2: the sha256 of the bundle a code gate's verdict is about, so a verdict is never reused for
other bytes and E14.1 can check that every piece of evidence is on the same bundle. Null for the
gates that run before any bundle exists (ISATVON_CONTRACT, LIBRARIES, SCHEMA_SQL).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_gate_result_bundle_sha256"
down_revision: str | Sequence[str] | None = "0039_workflow_manifests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gate_results",
        sa.Column("bundle_sha256", sa.String(length=64), nullable=True),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("gate_results", "bundle_sha256", schema="sbam")
