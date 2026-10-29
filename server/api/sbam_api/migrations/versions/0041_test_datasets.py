"""test_datasets

Revision ID: 0041_test_datasets
Revises: 0040_gate_result_bundle_sha256
Create Date: 2026-10-29

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E13.4: a Sub-Skill's synthetic test data. `test_datasets` holds one row per version (an FDE edit
writes a new version, never changes one); `test_cases` holds that version's cases.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041_test_datasets"
down_revision: str | Sequence[str] | None = "0040_gate_result_bundle_sha256"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _base_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "test_datasets",
        sa.Column("sub_skill_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("files_keys", JSON, nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("edited_by_fde", sa.Boolean(), nullable=False),
        *_base_columns(),
        sa.CheckConstraint("version >= 1", name=op.f("ck_test_datasets_version_positive")),
        sa.CheckConstraint("synthetic", name=op.f("ck_test_datasets_synthetic_always")),
        sa.ForeignKeyConstraint(
            ["sub_skill_id"],
            ["sbam.sub_skills.id"],
            name=op.f("fk_test_datasets_sub_skill_id_sub_skills"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_datasets")),
        sa.UniqueConstraint(
            "sub_skill_id", "version", name=op.f("uq_test_datasets_sub_skill_id_version")
        ),
        schema="sbam",
    )
    for column in ("project_id", "tenant_id", "sub_skill_id"):
        op.create_index(
            op.f(f"ix_test_datasets_{column}"),
            "test_datasets",
            [column],
            unique=False,
            schema="sbam",
        )
    op.create_table(
        "test_cases",
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("input", JSON, nullable=False),
        sa.Column("initial_state", JSON, nullable=False),
        sa.Column("state_script", JSON, nullable=False),
        sa.Column("expected_terminal", sa.String(length=10), nullable=False),
        sa.Column("expected_output", JSON, nullable=True),
        sa.Column("assertions", JSON, nullable=False),
        *_base_columns(),
        sa.CheckConstraint(
            "kind IN ('PASS_FIRST', 'PASS_AFTER_RETRY', 'ESCALATION', 'NO_OP', 'EDGE', 'FDE')",
            name=op.f("ck_test_cases_kind_valid"),
        ),
        sa.CheckConstraint(
            "expected_terminal IN ('SUCCESS', 'ESCALATED', 'NO_OP')",
            name=op.f("ck_test_cases_expected_terminal_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["sbam.test_datasets.id"],
            name=op.f("fk_test_cases_dataset_id_test_datasets"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_cases")),
        sa.UniqueConstraint("dataset_id", "name", name=op.f("uq_test_cases_dataset_id_name")),
        schema="sbam",
    )
    for column in ("project_id", "tenant_id", "dataset_id"):
        op.create_index(
            op.f(f"ix_test_cases_{column}"),
            "test_cases",
            [column],
            unique=False,
            schema="sbam",
        )


def downgrade() -> None:
    for column in ("dataset_id", "tenant_id", "project_id"):
        op.drop_index(op.f(f"ix_test_cases_{column}"), table_name="test_cases", schema="sbam")
    op.drop_table("test_cases", schema="sbam")
    for column in ("sub_skill_id", "tenant_id", "project_id"):
        op.drop_index(op.f(f"ix_test_datasets_{column}"), table_name="test_datasets", schema="sbam")
    op.drop_table("test_datasets", schema="sbam")
