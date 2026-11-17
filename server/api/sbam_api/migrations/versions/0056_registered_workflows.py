"""registered_workflows

Revision ID: 0056_registered_workflows
Revises: 0055_gate_result_review
Create Date: 2026-11-17

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#760 (WG-12): SBAM's own record of what is registered in Bifrost, the source of truth for the
registry (ADR 0028, decision 4). A row is inserted when a certified, frozen Workflow is registered
and changes once, REGISTERED to SUPERSEDED; rows are never deleted (no revocation, C-31).
`(tenant, workflow, version)` and `(tenant, registry_name)` are unique, which makes registering
idempotent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056_registered_workflows"
down_revision: str | Sequence[str] | None = "0055_gate_result_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
INDEXED = ("workflow_id", "certification_id", "project_id", "tenant_id")


def upgrade() -> None:
    op.create_table(
        "registered_workflows",
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("registry_name", sa.String(length=64), nullable=False),
        sa.Column("registry_id", sa.String(length=160), nullable=False),
        sa.Column("image_digest", sa.String(length=71), nullable=False),
        sa.Column("manifest_hash", sa.String(length=71), nullable=False),
        sa.Column("certification_id", sa.Uuid(), nullable=False),
        sa.Column("owner", sa.String(length=160), nullable=False),
        sa.Column("tools", JSON, nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.String(length=20), nullable=True),
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
            "status IN ('REGISTERED', 'SUPERSEDED')",
            name=op.f("ck_registered_workflows_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["certification_id"],
            ["sbam.certifications.id"],
            name=op.f("fk_registered_workflows_certification_id_certifications"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["sbam.workflows.id"],
            name=op.f("fk_registered_workflows_workflow_id_workflows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registered_workflows")),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_id",
            "version",
            name="uq_registered_workflows_version",
        ),
        sa.UniqueConstraint("tenant_id", "registry_name", name="uq_registered_workflows_name"),
        schema="sbam",
    )
    for column in INDEXED:
        op.create_index(
            op.f(f"ix_registered_workflows_{column}"),
            "registered_workflows",
            [column],
            unique=False,
            schema="sbam",
        )


def downgrade() -> None:
    for column in reversed(INDEXED):
        op.drop_index(
            op.f(f"ix_registered_workflows_{column}"),
            table_name="registered_workflows",
            schema="sbam",
        )
    op.drop_table("registered_workflows", schema="sbam")
