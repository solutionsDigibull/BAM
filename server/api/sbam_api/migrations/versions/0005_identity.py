"""identity tenants and users

Revision ID: 0005_identity
Revises: 0004_runtime_registry
Create Date: 2026-10-13

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_identity"
down_revision: str | Sequence[str] | None = "0004_runtime_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("keycloak_org_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED')", name=op.f("ck_tenants_status_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("keycloak_org_id", name=op.f("uq_tenants_keycloak_org_id")),
        schema="sbam",
    )
    op.create_index(
        "uq_tenants_name_ci",
        "tenants",
        [sa.literal_column("lower(name)")],
        unique=True,
        schema="sbam",
    )
    op.create_table(
        "users",
        sa.Column("keycloak_sub", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("portal_roles", postgresql.ARRAY(sa.String(length=40)), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_session_id", sa.String(length=64), nullable=True),
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
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_users_row_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("keycloak_sub", name=op.f("uq_users_keycloak_sub")),
        schema="sbam",
    )
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=False, schema="sbam")


def downgrade() -> None:
    op.drop_index(op.f("ix_users_tenant_id"), table_name="users", schema="sbam")
    op.drop_table("users", schema="sbam")
    op.drop_index("uq_tenants_name_ci", table_name="tenants", schema="sbam")
    op.drop_table("tenants", schema="sbam")
