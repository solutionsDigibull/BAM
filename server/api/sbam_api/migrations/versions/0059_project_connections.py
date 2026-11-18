"""project_connections

Revision ID: 0059_project_connections
Revises: 0058_masters_0_3
Create Date: 2026-11-18

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#764 (WG-16, ADR 0030): one row per environment variable of a project. A value is stored only
encrypted (AES-256-GCM): `ciphertext`, `key_id` and `masked_tail` are all null or all set (CHECK).
Downgrade drops the table and with it every stored value; they cannot be recovered, which is the
point of a secret store.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059_project_connections"
down_revision: str | Sequence[str] | None = "0058_masters_0_3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
INDEXED = ("project_id", "tenant_id")


def upgrade() -> None:
    op.create_table(
        "project_connections",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("purpose", sa.String(length=500), nullable=False),
        sa.Column("where_to_get", sa.String(length=500), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("key_id", sa.String(length=32), nullable=True),
        sa.Column("masked_tail", sa.String(length=4), nullable=True),
        sa.Column("value_version", sa.Integer(), nullable=False),
        sa.Column("value_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("value_set_by", sa.String(length=255), nullable=True),
        sa.Column("test_mode", sa.String(length=10), nullable=False),
        sa.Column("test_config", JSON, nullable=True),
        sa.Column("tested_value_version", sa.Integer(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_code", sa.String(length=20), nullable=True),
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
        sa.CheckConstraint(
            "source IN ('ISATVON', 'REPO', 'FDE')",
            name=op.f("ck_project_connections_source_valid"),
        ),
        sa.CheckConstraint(
            "test_mode IN ('HTTP', 'NONE')",
            name=op.f("ck_project_connections_test_mode_valid"),
        ),
        sa.CheckConstraint(
            "last_test_code IN ('OK', 'UNAUTHORIZED', 'NOT_FOUND', 'RATE_LIMITED', 'SERVER_ERROR',"
            " 'REDIRECT', 'UNREACHABLE', 'TIMEOUT', 'TLS_ERROR', 'BLOCKED_HOST')",
            name=op.f("ck_project_connections_last_test_code_valid"),
        ),
        sa.CheckConstraint(
            "(ciphertext IS NULL AND key_id IS NULL AND masked_tail IS NULL)"
            " OR (ciphertext IS NOT NULL AND key_id IS NOT NULL AND masked_tail IS NOT NULL)",
            name=op.f("ck_project_connections_value_all_or_none"),
        ),
        sa.CheckConstraint(
            "value_version >= 0", name=op.f("ck_project_connections_value_version_not_negative")
        ),
        sa.CheckConstraint(
            "row_version >= 1", name=op.f("ck_project_connections_row_version_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_connections")),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "name",
            name=op.f("uq_project_connections_tenant_id_project_id_name"),
        ),
        schema="sbam",
    )
    for column in INDEXED:
        op.create_index(
            op.f(f"ix_project_connections_{column}"),
            "project_connections",
            [column],
            unique=False,
            schema="sbam",
        )


def downgrade() -> None:
    for column in reversed(INDEXED):
        op.drop_index(
            op.f(f"ix_project_connections_{column}"),
            table_name="project_connections",
            schema="sbam",
        )
    op.drop_table("project_connections", schema="sbam")
