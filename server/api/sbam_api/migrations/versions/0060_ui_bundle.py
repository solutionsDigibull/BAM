"""UI bundle evidence: the UI_BUNDLE gate and the certified bundle on a registry entry

Revision ID: 0060_ui_bundle
Revises: 0059_project_connections
Create Date: 2026-11-18

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#857 (WG-25): the Workflow manifest carries a UI bundle entry, certification needs a Workflow-level
`UI_BUNDLE` row in `gate_results`, and the registry lookup returns the certified entry. This widens
`ck_gate_results_gate_valid` to `build_dashboard.GateName` as of this revision and adds the nullable
`registered_workflows.ui_bundle` (a copy of the manifest's entry; null on older entries). Downgrade
deletes the UI_BUNDLE rows first (the old constraint would refuse them), then restores both.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060_ui_bundle"
down_revision: str | Sequence[str] | None = "0059_project_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_gate_results_gate_valid"  # op.f() in _set: needs a running migration
_OLD = (
    "'ISATVON_CONTRACT', 'LIBRARIES', 'SCHEMA_SQL', 'CODEGEN', 'AST_SYNTAX', 'AST_SKELETON', "
    "'AST_CONFORMANCE', 'AST_DECOUPLING', 'AST_NO_MANUAL_LOOP', 'AST_HANDOFF', "
    "'AST_IMPORTS', 'AST_SQL_SAFETY', 'RUFF', 'BLACK', 'MYPY', 'CLAMAV', 'BANDIT', "
    "'IMPORT_RESOLUTION', 'REPO_LICENCE', 'DEPENDENCY_VULN', 'MCP_COMPLIANCE', 'REVIEW', "
    "'LOOP_TESTS'"
)
_NEW = (
    "'ISATVON_CONTRACT', 'LIBRARIES', 'SCHEMA_SQL', 'CODEGEN', 'AST_SYNTAX', 'AST_SKELETON', "
    "'AST_CONFORMANCE', 'AST_DECOUPLING', 'AST_NO_MANUAL_LOOP', 'AST_HANDOFF', "
    "'AST_IMPORTS', 'AST_SQL_SAFETY', 'RUFF', 'BLACK', 'MYPY', 'CLAMAV', 'BANDIT', "
    "'IMPORT_RESOLUTION', 'REPO_LICENCE', 'DEPENDENCY_VULN', 'MCP_COMPLIANCE', 'UI_BUNDLE', "
    "'REVIEW', 'LOOP_TESTS'"
)


def _set(values: str) -> None:
    name = op.f(_NAME)  # already the full name, no convention prefix
    op.drop_constraint(name, "gate_results", schema="sbam", type_="check")
    op.create_check_constraint(name, "gate_results", f"gate IN ({values})", schema="sbam")


def upgrade() -> None:
    _set(_NEW)
    op.add_column(
        "registered_workflows",
        sa.Column("ui_bundle", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
        schema="sbam",
    )


def downgrade() -> None:
    op.drop_column("registered_workflows", "ui_bundle", schema="sbam")
    op.execute("DELETE FROM sbam.gate_results WHERE gate = 'UI_BUNDLE'")
    _set(_OLD)
