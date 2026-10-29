"""gate_result_stage

Revision ID: 0038_gate_result_stage
Revises: 0037_llm_derivatives
Create Date: 2026-10-28

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E9.3: a gate result's build stage key, one result per gate per stage (LIBRARIES runs before
any code_attempts row exists).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_gate_result_stage"
down_revision: str | Sequence[str] | None = "0037_llm_derivatives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gate_results", sa.Column("build_no", sa.Integer(), nullable=True), schema="sbam")
    op.add_column("gate_results", sa.Column("cycle", sa.Integer(), nullable=True), schema="sbam")
    op.add_column("gate_results", sa.Column("attempt", sa.Integer(), nullable=True), schema="sbam")
    op.create_unique_constraint(
        op.f("uq_gate_results_sub_skill_id_gate_build_no_cycle_attempt"),
        "gate_results",
        ["sub_skill_id", "gate", "build_no", "cycle", "attempt"],
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_gate_results_sub_skill_id_gate_build_no_cycle_attempt"),
        "gate_results",
        schema="sbam",
        type_="unique",
    )
    op.drop_column("gate_results", "attempt", schema="sbam")
    op.drop_column("gate_results", "cycle", schema="sbam")
    op.drop_column("gate_results", "build_no", schema="sbam")
