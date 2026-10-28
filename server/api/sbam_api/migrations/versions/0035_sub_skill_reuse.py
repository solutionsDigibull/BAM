"""sub_skill_reuse

Revision ID: 0035_sub_skill_reuse
Revises: 0034_sow_impacts
Create Date: 2026-10-28

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E7.2: a Sub-Skill copied into a new Workflow version names the one it reuses.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_sub_skill_reuse"
down_revision: str | Sequence[str] | None = "0034_sow_impacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sub_skills", sa.Column("reused_from_id", sa.Uuid(), nullable=True), schema="sbam"
    )
    op.create_foreign_key(
        op.f("fk_sub_skills_reused_from_id_sub_skills"),
        "sub_skills",
        "sub_skills",
        ["reused_from_id"],
        ["id"],
        source_schema="sbam",
        referent_schema="sbam",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_sub_skills_reused_from_id_sub_skills"),
        "sub_skills",
        schema="sbam",
        type_="foreignkey",
    )
    op.drop_column("sub_skills", "reused_from_id", schema="sbam")
