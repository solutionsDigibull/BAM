"""runtime_registries + runtime_registry_packages: skill-runtime image contents (E12.1)

Revision ID: 0004_runtime_registry
Revises: 0003_audit_log_outbox
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_runtime_registry"
down_revision: str | Sequence[str] | None = "0003_audit_log_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_registries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("image_digest", sa.String(length=71), nullable=False),
        sa.Column("python_version", sa.String(length=20), nullable=False),
        sa.Column("content_sha256", sa.String(length=71), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_registries")),
        sa.UniqueConstraint("image_digest", name=op.f("uq_runtime_registries_image_digest")),
        schema="sbam",
    )
    op.create_index(
        "ix_runtime_registries_one_active",
        "runtime_registries",
        ["is_active"],
        unique=True,
        schema="sbam",
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "runtime_registry_packages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("registry_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("top_level_modules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["registry_id"],
            ["sbam.runtime_registries.id"],
            name=op.f("fk_runtime_registry_packages_registry_id_runtime_registries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runtime_registry_packages")),
        sa.UniqueConstraint(
            "registry_id", "name", name=op.f("uq_runtime_registry_packages_registry_id_name")
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_runtime_registry_packages_registry_id"),
        "runtime_registry_packages",
        ["registry_id"],
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_runtime_registry_packages_registry_id"),
        table_name="runtime_registry_packages",
        schema="sbam",
    )
    op.drop_table("runtime_registry_packages", schema="sbam")
    op.drop_index(
        "ix_runtime_registries_one_active", table_name="runtime_registries", schema="sbam"
    )
    op.drop_table("runtime_registries", schema="sbam")
