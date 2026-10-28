"""sub_skill_manifests, gate_results and sub_skills.validated_at

Revision ID: 0033_skill_manifests
Revises: 0032_sub_skill_edits
Create Date: 2026-10-28

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E9.1: the Skill Manifest of each build stage key (frozen rows can't change: the shared
`forbid_frozen_change` trigger from 0001), the gate results (ISATVON_CONTRACT first), and when a
Sub-Skill passed validation (the manifest's timestamps).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sbam_api.db.frozen import create_frozen_trigger, drop_frozen_trigger
from sqlalchemy.dialects import postgresql

_GATES = (
    "'ISATVON_CONTRACT', 'LIBRARIES', 'SCHEMA_SQL', 'CODEGEN', 'AST_SYNTAX', 'AST_SKELETON', "
    "'AST_CONFORMANCE', 'AST_DECOUPLING', 'AST_NO_MANUAL_LOOP', 'AST_HANDOFF', "
    "'AST_IMPORTS', 'AST_SQL_SAFETY', 'RUFF', 'BLACK', 'MYPY', 'CLAMAV', 'BANDIT', "
    "'IMPORT_RESOLUTION', 'LOOP_TESTS'"
)  # build_dashboard.GateName as of this revision

revision: str = "0033_skill_manifests"
down_revision: str | Sequence[str] | None = "0032_sub_skill_edits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gate_results",
        sa.Column("sub_skill_id", sa.Uuid(), nullable=False),
        sa.Column("code_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("gate", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "findings",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("tool_version", sa.String(length=80), nullable=True),
        sa.Column("config_sha256", sa.String(length=71), nullable=True),
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
            f"gate IN ({_GATES})",
            name=op.f("ck_gate_results_gate_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PASSED', 'FAILED', 'SKIPPED')",
            name=op.f("ck_gate_results_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["code_attempt_id"],
            ["sbam.code_attempts.id"],
            name=op.f("fk_gate_results_code_attempt_id_code_attempts"),
        ),
        sa.ForeignKeyConstraint(
            ["sub_skill_id"],
            ["sbam.sub_skills.id"],
            name=op.f("fk_gate_results_sub_skill_id_sub_skills"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gate_results")),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_gate_results_project_id"),
        "gate_results",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_gate_results_sub_skill_id"),
        "gate_results",
        ["sub_skill_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_gate_results_tenant_id"),
        "gate_results",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    op.create_table(
        "sub_skill_manifests",
        sa.Column("sub_skill_id", sa.Uuid(), nullable=False),
        sa.Column("build_no", sa.Integer(), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "manifest",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(length=71), nullable=False),
        sa.Column("schema_version", sa.String(length=40), nullable=False),
        sa.Column("runtime_digest", sa.String(length=71), nullable=False),
        sa.Column("is_frozen", sa.Boolean(), server_default="false", nullable=False),
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
        sa.ForeignKeyConstraint(
            ["sub_skill_id"],
            ["sbam.sub_skills.id"],
            name=op.f("fk_sub_skill_manifests_sub_skill_id_sub_skills"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sub_skill_manifests")),
        sa.UniqueConstraint(
            "tenant_id",
            "sub_skill_id",
            "build_no",
            "cycle",
            "attempt",
            name=op.f("uq_sub_skill_manifests_stage_key"),
        ),
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sub_skill_manifests_project_id"),
        "sub_skill_manifests",
        ["project_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sub_skill_manifests_sub_skill_id"),
        "sub_skill_manifests",
        ["sub_skill_id"],
        unique=False,
        schema="sbam",
    )
    op.create_index(
        op.f("ix_sub_skill_manifests_tenant_id"),
        "sub_skill_manifests",
        ["tenant_id"],
        unique=False,
        schema="sbam",
    )
    op.execute(create_frozen_trigger("sub_skill_manifests"))
    op.add_column(
        "sub_skills",
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("sub_skills", "validated_at", schema="sbam")
    op.execute(drop_frozen_trigger("sub_skill_manifests"))
    op.drop_index(
        op.f("ix_sub_skill_manifests_tenant_id"), table_name="sub_skill_manifests", schema="sbam"
    )
    op.drop_index(
        op.f("ix_sub_skill_manifests_sub_skill_id"), table_name="sub_skill_manifests", schema="sbam"
    )
    op.drop_index(
        op.f("ix_sub_skill_manifests_project_id"), table_name="sub_skill_manifests", schema="sbam"
    )
    op.drop_table("sub_skill_manifests", schema="sbam")
    op.drop_index(op.f("ix_gate_results_tenant_id"), table_name="gate_results", schema="sbam")
    op.drop_index(op.f("ix_gate_results_sub_skill_id"), table_name="gate_results", schema="sbam")
    op.drop_index(op.f("ix_gate_results_project_id"), table_name="gate_results", schema="sbam")
    op.drop_table("gate_results", schema="sbam")
