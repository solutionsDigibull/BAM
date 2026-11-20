"""project_token_caps

Revision ID: 0066_project_token_caps
Revises: 0065_master_lists
Create Date: 2026-11-20

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#776 (WG-23, ADR 0028 decision 6): one row per project that has its own token caps; no row means the
platform defaults. `raised_builds` lists the builds whose cap was raised (once per build).
Downgrade drops the table: every project goes back to the platform defaults.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066_project_token_caps"
down_revision: str | Sequence[str] | None = "0065_master_lists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
INDEXED = ("project_id", "tenant_id")
MAX_CAP = 1_000_000_000


def _range(column: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} >= 1 AND {column} <= {MAX_CAP}",
        name=op.f(f"ck_project_token_caps_{column}_in_range"),
    )


def upgrade() -> None:
    op.create_table(
        "project_token_caps",
        sa.Column("sub_skill_tokens", sa.Integer(), nullable=False),
        sa.Column("workflow_tokens", sa.Integer(), nullable=False),
        sa.Column("knowledge_tokens", sa.Integer(), nullable=False),
        sa.Column("first_alert_pct", sa.Integer(), nullable=False),
        sa.Column("second_alert_pct", sa.Integer(), nullable=False),
        sa.Column("raised_builds", JSON, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _range("sub_skill_tokens"),
        _range("workflow_tokens"),
        _range("knowledge_tokens"),
        sa.CheckConstraint(
            "first_alert_pct >= 1 AND first_alert_pct < second_alert_pct"
            " AND second_alert_pct <= 99",
            name=op.f("ck_project_token_caps_alerts_ordered"),
        ),
        sa.CheckConstraint(
            "sub_skill_tokens <= workflow_tokens",
            name=op.f("ck_project_token_caps_sub_skill_within_workflow"),
        ),
        sa.CheckConstraint(
            "row_version >= 1", name=op.f("ck_project_token_caps_row_version_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_token_caps")),
        sa.UniqueConstraint(
            "tenant_id", "project_id", name=op.f("uq_project_token_caps_tenant_id_project_id")
        ),
        schema="sbam",
    )
    for column in INDEXED:
        op.create_index(
            op.f(f"ix_project_token_caps_{column}"),
            "project_token_caps",
            [column],
            unique=False,
            schema="sbam",
        )


def downgrade() -> None:
    for column in reversed(INDEXED):
        op.drop_index(
            op.f(f"ix_project_token_caps_{column}"), table_name="project_token_caps", schema="sbam"
        )
    op.drop_table("project_token_caps", schema="sbam")
