"""runtime registry: allowed flag per package

Revision ID: 0010_registry_allowed
Revises: 0009_document_scan
Create Date: 2026-10-15

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

`allowed` is part of a registry's content hash (E3.12), so registries synced before it existed no
longer match their stored `content_sha256`. They are kept (frozen versions still name their
digest) but deactivated: the next `make up` / CI sync of the rebuilt skill-runtime image activates
a registry that carries the flag. Until then the API answers 404 ("Registry not built yet").
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_registry_allowed"
down_revision: str | Sequence[str] | None = "0009_document_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_registry_packages",
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="sbam",
    )
    op.alter_column("runtime_registry_packages", "allowed", server_default=None, schema="sbam")
    op.execute("UPDATE sbam.runtime_registries SET is_active = false WHERE is_active")


def downgrade() -> None:
    op.drop_column("runtime_registry_packages", "allowed", schema="sbam")
