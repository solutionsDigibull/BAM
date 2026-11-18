"""Registry entry kind: staging (Commit) and production (promotion)

Revision ID: 0061_registry_entry_kind
Revises: 0060_ui_bundle
Create Date: 2026-11-18

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#860 (WG-26, ADR 0032): every `registered_workflows` row gets a `kind`, `staging` (Commit, the
assigned FDE only) or `production` (promotion, #148; the only kind SRIM's key may see). Rows that
exist before this revision were registered as certified entries for SRIM, so they become
`production`. A Workflow version may now have one row per kind, so `uq_registered_workflows_version`
gains `kind`. Downgrade deletes the staging rows first (the old unique would refuse a second row
of one version; Commit registers a staging entry again), then restores the old constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_registry_entry_kind"
down_revision: str | Sequence[str] | None = "0060_ui_bundle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE = "uq_registered_workflows_version"
_CHECK = "ck_registered_workflows_kind_valid"


def upgrade() -> None:
    op.add_column(
        "registered_workflows",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="production",
        ),
        schema="sbam",
    )
    op.create_check_constraint(
        op.f(_CHECK), "registered_workflows", "kind IN ('staging', 'production')", schema="sbam"
    )
    op.drop_constraint(_UNIQUE, "registered_workflows", schema="sbam", type_="unique")
    op.create_unique_constraint(
        _UNIQUE,
        "registered_workflows",
        ["tenant_id", "workflow_id", "version", "kind"],
        schema="sbam",
    )


def downgrade() -> None:
    op.execute("DELETE FROM sbam.registered_workflows WHERE kind = 'staging'")
    op.drop_constraint(_UNIQUE, "registered_workflows", schema="sbam", type_="unique")
    op.create_unique_constraint(
        _UNIQUE,
        "registered_workflows",
        ["tenant_id", "workflow_id", "version"],
        schema="sbam",
    )
    op.drop_constraint(op.f(_CHECK), "registered_workflows", schema="sbam", type_="check")
    op.drop_column("registered_workflows", "kind", schema="sbam")
