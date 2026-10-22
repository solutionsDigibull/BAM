"""code_bundles

Revision ID: 0022_code_bundles
Revises: 0021_master_documents
Create Date: 2026-10-22

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E11.3: the `artifacts` index (TD §7.8), the bundle hash and Black provenance on `code_attempts`, and
the ASSEMBLED / ASSEMBLY_FAILED statuses.

Renumbered on dev (#298) twice: first because #295 (0020_code_bundles) and #290
(0020_sow_versions_immutable) both branched from 0019_sow_versions; then again because #296
(0021_master_documents) landed on 0020_sow_versions_immutable before this fix merged. Re-chained
after 0021_master_documents. No `alembic merge` used.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_code_bundles"
down_revision: str | Sequence[str] | None = "0021_master_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_CHECK = "ck_code_attempts_status_valid"
OLD_STATUSES = "status IN ('BODIES_GENERATED', 'PRECHECK_FAILED')"
NEW_STATUSES = "status IN ('BODIES_GENERATED', 'PRECHECK_FAILED', 'ASSEMBLED', 'ASSEMBLY_FAILED')"


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("is_frozen", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("kind IN ('CODE_BUNDLE')", name=op.f("ck_artifacts_kind_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
        sa.UniqueConstraint(
            "tenant_id", "storage_key", name=op.f("uq_artifacts_tenant_id_storage_key")
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_artifacts_project_id"), "artifacts", ["project_id"], unique=False, schema="sbam"
    )
    op.create_index(
        op.f("ix_artifacts_tenant_id"), "artifacts", ["tenant_id"], unique=False, schema="sbam"
    )
    op.add_column(
        "code_attempts",
        sa.Column("bundle_sha256", sa.String(length=64), nullable=True),
        schema="sbam",
    )
    op.add_column(
        "code_attempts",
        sa.Column("black_version", sa.String(length=40), nullable=True),
        schema="sbam",
    )
    op.add_column(
        "code_attempts",
        sa.Column("black_config_sha256", sa.String(length=64), nullable=True),
        schema="sbam",
    )
    op.drop_constraint(op.f(STATUS_CHECK), "code_attempts", type_="check", schema="sbam")
    op.create_check_constraint(op.f(STATUS_CHECK), "code_attempts", NEW_STATUSES, schema="sbam")


def downgrade() -> None:
    # Assembly results go with their columns; the bodies they came from passed the pre-check.
    op.execute(
        "UPDATE sbam.code_attempts SET status = 'BODIES_GENERATED', bundle_key = NULL,"
        " files = NULL WHERE status IN ('ASSEMBLED', 'ASSEMBLY_FAILED')"
    )
    op.drop_constraint(op.f(STATUS_CHECK), "code_attempts", type_="check", schema="sbam")
    op.create_check_constraint(op.f(STATUS_CHECK), "code_attempts", OLD_STATUSES, schema="sbam")
    op.drop_column("code_attempts", "black_config_sha256", schema="sbam")
    op.drop_column("code_attempts", "black_version", schema="sbam")
    op.drop_column("code_attempts", "bundle_sha256", schema="sbam")
    op.drop_index(op.f("ix_artifacts_tenant_id"), table_name="artifacts", schema="sbam")
    op.drop_index(op.f("ix_artifacts_project_id"), table_name="artifacts", schema="sbam")
    op.drop_table("artifacts", schema="sbam")
