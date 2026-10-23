"""documents: the e-mail an attachment belongs to (E4.6)

Revision ID: 0026_document_parent
Revises: 0025_workflows
Create Date: 2026-10-23

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_document_parent"
down_revision: str | Sequence[str] | None = "0025_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("parent_document_id", sa.Uuid(), nullable=True), schema="sbam"
    )
    op.create_foreign_key(
        "fk_documents_parent_document_id_documents",
        "documents",
        "documents",
        ["parent_document_id"],
        ["id"],
        source_schema="sbam",
        referent_schema="sbam",
    )
    op.create_index(
        "ix_documents_parent_document_id", "documents", ["parent_document_id"], schema="sbam"
    )
    op.create_check_constraint(
        "ck_documents_parent_not_self",
        "documents",
        "parent_document_id IS NULL OR parent_document_id <> id",
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_constraint("ck_documents_parent_not_self", "documents", schema="sbam", type_="check")
    op.drop_index("ix_documents_parent_document_id", table_name="documents", schema="sbam")
    op.drop_constraint(
        "fk_documents_parent_document_id_documents", "documents", schema="sbam", type_="foreignkey"
    )
    op.drop_column("documents", "parent_document_id", schema="sbam")
