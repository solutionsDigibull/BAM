"""audit_log (append-only) and notification_outbox (E1.8)

Revision ID: 0003_audit_log_outbox
Revises: 0002_temporal_runs
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sbam_api.audit.sql import create_audit_guard, drop_audit_guard
from sqlalchemy.dialects import postgresql

revision: str = "0003_audit_log_outbox"
down_revision: str | Sequence[str] | None = "0002_temporal_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        sa.UniqueConstraint(
            "tenant_id",
            "entity_type",
            "entity_id",
            "idempotency_key",
            name=op.f("uq_audit_log_tenant_id_entity_type_entity_id_idempotency_key"),
        ),
        schema="sbam",
    )
    op.create_index(op.f("ix_audit_log_tenant_id"), "audit_log", ["tenant_id"], schema="sbam")
    op.create_index(op.f("ix_audit_log_at"), "audit_log", ["at"], schema="sbam")
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("audit_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["sbam.audit_log.id"],
            name=op.f("fk_notification_outbox_audit_id_audit_log"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_outbox")),
        schema="sbam",
    )
    for column in ("tenant_id", "dispatched_at"):
        op.create_index(
            op.f(f"ix_notification_outbox_{column}"), "notification_outbox", [column], schema="sbam"
        )
    for statement in create_audit_guard():
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_audit_guard():
        op.execute(statement)
    for column in ("dispatched_at", "tenant_id"):
        op.drop_index(
            op.f(f"ix_notification_outbox_{column}"),
            table_name="notification_outbox",
            schema="sbam",
        )
    op.drop_table("notification_outbox", schema="sbam")
    op.drop_index(op.f("ix_audit_log_at"), table_name="audit_log", schema="sbam")
    op.drop_index(op.f("ix_audit_log_tenant_id"), table_name="audit_log", schema="sbam")
    op.drop_table("audit_log", schema="sbam")
