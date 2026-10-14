"""documents, document versions and upload reservations

Revision ID: 0007_documents
Revises: 0006_projects
Create Date: 2026-10-14

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0007_documents"
down_revision: str | Sequence[str] | None = "0006_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERSION_STATES = (
    "'UPLOADED', 'SCANNING', 'REJECTED', 'SCAN_FAILED', 'PARSING', 'PARSED', 'PARSE_FAILED', "
    "'DELETED', 'PURGED'"
)
_RESERVATION_STATUSES = "'RESERVED', 'CREATED', 'FINISHED', 'DUPLICATE'"


def _owned_columns() -> list[sa.Column[Any]]:
    """The columns every project-owned table has (sbam_api.db.base.ProjectOwned)."""
    return [
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
        sa.Column("project_id", sa.Uuid(), nullable=False),
    ]


def _owned_indexes(table: str) -> None:
    op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"], schema="sbam")
    op.create_index(op.f(f"ix_{table}_project_id"), table, ["project_id"], schema="sbam")


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        *_owned_columns(),
        sa.CheckConstraint("row_version >= 1", name=op.f("ck_documents_row_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        schema="sbam",
    )
    _owned_indexes("documents")

    op.create_table(
        "document_versions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("ingestion_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        *_owned_columns(),
        sa.CheckConstraint(
            "row_version >= 1", name=op.f("ck_document_versions_row_version_positive")
        ),
        sa.CheckConstraint(
            f"state IN ({_VERSION_STATES})", name=op.f("ck_document_versions_state_valid")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["sbam.documents.id"],
            name=op.f("fk_document_versions_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        schema="sbam",
    )
    _owned_indexes("document_versions")
    op.create_index(
        op.f("ix_document_versions_document_id"),
        "document_versions",
        ["document_id"],
        schema="sbam",
    )
    op.create_index(
        op.f("ix_document_versions_state"), "document_versions", ["state"], schema="sbam"
    )
    op.create_index(
        "ix_document_versions_project_sha256",
        "document_versions",
        ["project_id", "sha256"],
        schema="sbam",
    )

    op.create_table(
        "upload_reservations",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=True),
        *_owned_columns(),
        sa.CheckConstraint(
            "row_version >= 1", name=op.f("ck_upload_reservations_row_version_positive")
        ),
        sa.CheckConstraint(
            f"status IN ({_RESERVATION_STATUSES})", name=op.f("ck_upload_reservations_status_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_upload_reservations")),
        schema="sbam",
    )
    _owned_indexes("upload_reservations")
    op.create_index(
        op.f("ix_upload_reservations_batch_id"), "upload_reservations", ["batch_id"], schema="sbam"
    )
    op.create_index(
        "ix_upload_reservations_project_status",
        "upload_reservations",
        ["project_id", "status"],
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_table("upload_reservations", schema="sbam")
    op.drop_table("document_versions", schema="sbam")
    op.drop_table("documents", schema="sbam")
