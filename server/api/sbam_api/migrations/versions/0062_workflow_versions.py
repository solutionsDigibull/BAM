"""workflow_versions and the frozen trigger on artifacts

Revision ID: 0062_workflow_versions
Revises: 0061_registry_entry_kind
Create Date: 2026-11-19

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#132 (E14.4, TD §7, §11.4): `workflow_versions` records each frozen Workflow version
(MAJOR.0.PATCH). A row is inserted already frozen (`is_frozen` is always true), so the shared
`forbid_frozen_change` trigger refuses any later UPDATE or DELETE, for every database role. The same
trigger is added to `artifacts`: the file index rows of a frozen version are marked `is_frozen`
by the freeze and can no longer change. The other frozen tables (sub_skill_manifests,
workflow_manifests, certifications, sandbox_runs) already have it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sbam_api.db.frozen import create_frozen_trigger, drop_frozen_trigger

revision: str = "0062_workflow_versions"
down_revision: str | Sequence[str] | None = "0061_registry_entry_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_versions",
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("major", sa.Integer(), nullable=False),
        sa.Column("patch", sa.Integer(), nullable=False),
        sa.Column("sow_version_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_manifest_id", sa.Uuid(), nullable=False),
        sa.Column("change_reason", sa.String(length=500), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_frozen", sa.Boolean(), server_default="true", nullable=False),
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
        sa.CheckConstraint("is_frozen", name=op.f("ck_workflow_versions_always_frozen")),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["sbam.workflows.id"],
            name=op.f("fk_workflow_versions_workflow_id_workflows"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_manifest_id"],
            ["sbam.workflow_manifests.id"],
            name=op.f("fk_workflow_versions_workflow_manifest_id_workflow_manifests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_versions")),
        sa.UniqueConstraint(
            "workflow_id", "version", name=op.f("uq_workflow_versions_workflow_id")
        ),
        schema="sbam",
    )
    for column in ("project_id", "tenant_id", "workflow_id"):
        op.create_index(
            op.f(f"ix_workflow_versions_{column}"),
            "workflow_versions",
            [column],
            unique=False,
            schema="sbam",
        )
    op.execute(create_frozen_trigger("workflow_versions"))
    op.execute(create_frozen_trigger("artifacts"))


def downgrade() -> None:
    op.execute(drop_frozen_trigger("artifacts"))
    op.execute(drop_frozen_trigger("workflow_versions"))
    for column in ("workflow_id", "tenant_id", "project_id"):
        op.drop_index(
            op.f(f"ix_workflow_versions_{column}"), table_name="workflow_versions", schema="sbam"
        )
    op.drop_table("workflow_versions", schema="sbam")
