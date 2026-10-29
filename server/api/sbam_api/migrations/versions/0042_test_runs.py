"""test_runs

Revision ID: 0042_test_runs
Revises: 0041_test_datasets
Create Date: 2026-10-29

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E13.6: the Promptfoo result of a sandbox run, one row per `sandbox_run_id`, inserted and never
changed. `sandbox_run_id` is a plain id (the sandbox adapter owns `sandbox_runs`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_test_runs"
down_revision: str | Sequence[str] | None = "0041_test_datasets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "test_runs",
        sa.Column("sub_skill_id", sa.Uuid(), nullable=False),
        sa.Column("code_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("sandbox_run_id", sa.Uuid(), nullable=False),
        sa.Column("suite_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("non_certifying", sa.Boolean(), nullable=False),
        sa.Column("results", JSON, nullable=False),
        sa.Column("report_key", sa.String(length=1024), nullable=True),
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
            "state IN ('PASS', 'FAIL', 'NON_CERTIFYING', 'BLOCKED')",
            name=op.f("ck_test_runs_state_valid"),
        ),
        sa.CheckConstraint(
            "total >= 0 AND passed >= 0 AND failed >= 0",
            name=op.f("ck_test_runs_counts_not_negative"),
        ),
        sa.CheckConstraint("passed + failed = total", name=op.f("ck_test_runs_counts_add_up")),
        sa.CheckConstraint(
            "state <> 'PASS' OR (failed = 0 AND NOT non_certifying)",
            name=op.f("ck_test_runs_pass_is_green"),
        ),
        sa.ForeignKeyConstraint(
            ["code_attempt_id"],
            ["sbam.code_attempts.id"],
            name=op.f("fk_test_runs_code_attempt_id_code_attempts"),
        ),
        sa.ForeignKeyConstraint(
            ["sub_skill_id"],
            ["sbam.sub_skills.id"],
            name=op.f("fk_test_runs_sub_skill_id_sub_skills"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_runs")),
        sa.UniqueConstraint("sandbox_run_id", name=op.f("uq_test_runs_sandbox_run_id")),
        schema="sbam",
    )
    for column in ("project_id", "tenant_id", "sub_skill_id", "code_attempt_id"):
        op.create_index(
            op.f(f"ix_test_runs_{column}"), "test_runs", [column], unique=False, schema="sbam"
        )


def downgrade() -> None:
    for column in ("code_attempt_id", "sub_skill_id", "tenant_id", "project_id"):
        op.drop_index(op.f(f"ix_test_runs_{column}"), table_name="test_runs", schema="sbam")
    op.drop_table("test_runs", schema="sbam")
