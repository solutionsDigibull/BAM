"""baseline: schema sbam and the frozen-row trigger function

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-12

Feature tables arrive in their own modules' revisions. The schema itself is created by env.py (the
version table lives in it) and is never dropped here: a downgrade must not drop anything it didn't
create in this revision.
"""

from collections.abc import Sequence

from alembic import op
from sbam_api.db.frozen import create_frozen_function, drop_frozen_function

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(create_frozen_function())


def downgrade() -> None:
    op.execute(drop_frozen_function())
