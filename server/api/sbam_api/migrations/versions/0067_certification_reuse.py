"""certification_reuse

Revision ID: 0067_certification_reuse
Revises: 0066_project_token_caps
Create Date: 2026-11-21

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

E14.3 (#131): `certifications.reused_from_id`, the certificate an unchanged Sub-Skill carried over.
It is a composite foreign key on (tenant_id, project_id, id), so a reused certificate can only name
one of the same tenant and project; NULL (all existing rows) is not checked. Adding a nullable
column does not fire the freeze trigger. Downgrade drops the key, the unique constraint it needs
and the column: the certificates themselves stay.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_certification_reuse"
down_revision: str | Sequence[str] | None = "0066_project_token_caps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "certifications", sa.Column("reused_from_id", sa.Uuid(), nullable=True), schema="sbam"
    )
    op.create_unique_constraint(
        op.f("uq_certifications_tenant_project_id"),
        "certifications",
        ["tenant_id", "project_id", "id"],
        schema="sbam",
    )
    op.create_foreign_key(
        op.f("fk_certifications_reused_from_id_certifications"),
        "certifications",
        "certifications",
        ["tenant_id", "project_id", "reused_from_id"],
        ["tenant_id", "project_id", "id"],
        source_schema="sbam",
        referent_schema="sbam",
    )
    op.create_check_constraint(
        op.f("ck_certifications_reused_from_is_another"),
        "certifications",
        "reused_from_id IS NULL OR reused_from_id <> id",
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_certifications_reused_from_is_another"),
        "certifications",
        schema="sbam",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_certifications_reused_from_id_certifications"),
        "certifications",
        schema="sbam",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_certifications_tenant_project_id"),
        "certifications",
        schema="sbam",
        type_="unique",
    )
    op.drop_column("certifications", "reused_from_id", schema="sbam")
