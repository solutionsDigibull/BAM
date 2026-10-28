"""gate_results

Revision ID: 0032_gate_results
Revises: 0031_sub_skills
Create Date: 2026-10-27

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E12.2: one row per gate check of one code attempt's bundle (AST_SYNTAX, AST_SKELETON,
AST_CONFORMANCE; E12.x and the static gates reuse it), keyed to the bundle_sha256 it judged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_gate_results"
down_revision: str | Sequence[str] | None = "0031_sub_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GATES = (
    "ISATVON_CONTRACT",
    "LIBRARIES",
    "SCHEMA_SQL",
    "CODEGEN",
    "AST_SYNTAX",
    "AST_SKELETON",
    "AST_CONFORMANCE",
    "AST_DECOUPLING",
    "AST_NO_MANUAL_LOOP",
    "AST_HANDOFF",
    "AST_IMPORTS",
    "AST_SQL_SAFETY",
    "RUFF",
    "BLACK",
    "MYPY",
    "CLAMAV",
    "BANDIT",
    "IMPORT_RESOLUTION",
    "LOOP_TESTS",
)
STATUSES = ("PENDING", "RUNNING", "PASSED", "FAILED", "SKIPPED")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "gate_results",
        sa.Column("code_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("gate", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "findings",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("tool_version", sa.String(length=60), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("bundle_sha256", sa.String(length=64), nullable=False),
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
            f"status IN ({_in(STATUSES)})", name=op.f("ck_gate_results_status_valid")
        ),
        sa.CheckConstraint(f"gate IN ({_in(GATES)})", name=op.f("ck_gate_results_gate_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gate_results")),
        sa.UniqueConstraint(
            "tenant_id",
            "code_attempt_id",
            "gate",
            "bundle_sha256",
            name=op.f("uq_gate_results_tenant_id_code_attempt_id_gate_bundle_sha256"),
        ),
        schema="sbam",
    )
    for column in ("code_attempt_id", "project_id", "tenant_id"):
        op.create_index(
            op.f(f"ix_gate_results_{column}"),
            "gate_results",
            [column],
            unique=False,
            schema="sbam",
        )


def downgrade() -> None:
    for column in ("tenant_id", "project_id", "code_attempt_id"):
        op.drop_index(op.f(f"ix_gate_results_{column}"), table_name="gate_results", schema="sbam")
    op.drop_table("gate_results", schema="sbam")
