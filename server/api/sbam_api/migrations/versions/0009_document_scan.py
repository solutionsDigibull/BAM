"""document version scan columns

Revision ID: 0009_document_scan
Revises: 0008_rbac
Create Date: 2026-10-15

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_document_scan"
down_revision: str | Sequence[str] | None = "0008_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_document_versions_scan_status_valid"


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("scan_status", sa.String(length=20), nullable=True),
        schema="sbam",
    )
    op.add_column(
        "document_versions",
        sa.Column("scan_signature_version", sa.String(length=100), nullable=True),
        schema="sbam",
    )
    op.add_column(
        "document_versions",
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        schema="sbam",
    )
    op.add_column(
        "document_versions",
        sa.Column("reject_code", sa.String(length=40), nullable=True),
        schema="sbam",
    )
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "document_versions",
        "scan_status IN ('CLEAN', 'INFECTED', 'SCAN_FAILED')",
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "document_versions", schema="sbam", type_="check")
    for column in ("reject_code", "scanned_at", "scan_signature_version", "scan_status"):
        op.drop_column("document_versions", column, schema="sbam")
