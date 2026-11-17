"""gate_results: the licence, dependency vulnerability and MCP compliance gates

Revision ID: 0057_gate_result_compliance
Revises: 0056_registered_workflows
Create Date: 2026-11-17

Rules (.claude/rules/shared-files.md): one revision per PR, generated after rebasing on dev; touch
only your module's tables; downgrade() undoes exactly this revision and nothing older.

#761 (WG-13): certification needs the licence result, the dependency vulnerability result and the
MCP compliance result on the same bundle hash as the other gates, so each is a `gate_results` row:
`REPO_LICENCE`, `DEPENDENCY_VULN` and `MCP_COMPLIANCE`. This widens `ck_gate_results_gate_valid` to
`build_dashboard.GateName` as of this revision. Downgrade deletes the new rows first (the old
constraint would refuse them), then restores the old list.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0057_gate_result_compliance"
down_revision: str | Sequence[str] | None = "0056_registered_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = op.f("ck_gate_results_gate_valid")  # op.f: already the full name, no convention prefix
_OLD = (
    "'ISATVON_CONTRACT', 'LIBRARIES', 'SCHEMA_SQL', 'CODEGEN', 'AST_SYNTAX', 'AST_SKELETON', "
    "'AST_CONFORMANCE', 'AST_DECOUPLING', 'AST_NO_MANUAL_LOOP', 'AST_HANDOFF', "
    "'AST_IMPORTS', 'AST_SQL_SAFETY', 'RUFF', 'BLACK', 'MYPY', 'CLAMAV', 'BANDIT', "
    "'IMPORT_RESOLUTION', 'REVIEW', 'LOOP_TESTS'"
)
_NEW = (
    "'ISATVON_CONTRACT', 'LIBRARIES', 'SCHEMA_SQL', 'CODEGEN', 'AST_SYNTAX', 'AST_SKELETON', "
    "'AST_CONFORMANCE', 'AST_DECOUPLING', 'AST_NO_MANUAL_LOOP', 'AST_HANDOFF', "
    "'AST_IMPORTS', 'AST_SQL_SAFETY', 'RUFF', 'BLACK', 'MYPY', 'CLAMAV', 'BANDIT', "
    "'IMPORT_RESOLUTION', 'REPO_LICENCE', 'DEPENDENCY_VULN', 'MCP_COMPLIANCE', 'REVIEW', "
    "'LOOP_TESTS'"
)


def _set(values: str) -> None:
    op.drop_constraint(_NAME, "gate_results", schema="sbam", type_="check")
    op.create_check_constraint(_NAME, "gate_results", f"gate IN ({values})", schema="sbam")


def upgrade() -> None:
    _set(_NEW)


def downgrade() -> None:
    op.execute(
        "DELETE FROM sbam.gate_results "
        "WHERE gate IN ('REPO_LICENCE', 'DEPENDENCY_VULN', 'MCP_COMPLIANCE')"
    )
    _set(_OLD)
